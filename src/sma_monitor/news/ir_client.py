"""Company investor-relations news adapter.

Exact issuer URLs are optional per sidecar. RSS/Atom feeds are parsed directly;
HTML press-release pages are treated as a conservative link source. The output
uses ExaResult so the rest of the news pipeline can store and score it without a
separate persistence path.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET

import httpx

from ..portfolio.schema import Holding
from .exa_client import ExaResult

DEFAULT_USER_AGENT = "AI-CRO/1.0 (official issuer news monitor)"
PRESS_LINK_KEYWORDS = (
    "press",
    "release",
    "news",
    "investor",
    "corporate",
    "announc",
)


class IrError(RuntimeError):
    pass


@dataclass(frozen=True)
class _Link:
    href: str
    text: str


def search(
    holding: Holding,
    *,
    num_results: int = 5,
    user_agent: str = DEFAULT_USER_AGENT,
    client: httpx.Client | None = None,
) -> list[ExaResult]:
    """Fetch official IR/RSS links configured on one holding."""
    urls = _configured_urls(holding)
    if not urls:
        return []

    owns = client is None
    client = client or httpx.Client(timeout=30.0, follow_redirects=True)
    try:
        results: list[ExaResult] = []
        if holding.press_release_rss_url:
            results.extend(_fetch_feed(
                holding.press_release_rss_url,
                client=client,
                user_agent=user_agent,
                num_results=num_results,
            ))
        for url in (holding.press_releases_url, holding.ir_url):
            if url:
                results.extend(_fetch_html_links(
                    url,
                    client=client,
                    user_agent=user_agent,
                    num_results=num_results,
                ))
        return _dedupe(results)[:num_results]
    finally:
        if owns:
            client.close()


def configured(holding: Holding) -> bool:
    return bool(_configured_urls(holding))


def _configured_urls(holding: Holding) -> list[str]:
    return [
        str(u).strip()
        for u in (holding.press_release_rss_url, holding.press_releases_url, holding.ir_url)
        if str(u or "").strip()
    ]


def _fetch_feed(
    url: str,
    *,
    client: httpx.Client,
    user_agent: str,
    num_results: int,
) -> list[ExaResult]:
    body = _get_text(url, client=client, user_agent=user_agent)
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise IrError(f"IR feed parse failed for {url}: {e}") from e
    return _parse_feed(root, source_url=url)[:num_results]


def _fetch_html_links(
    url: str,
    *,
    client: httpx.Client,
    user_agent: str,
    num_results: int,
) -> list[ExaResult]:
    body = _get_text(url, client=client, user_agent=user_agent)
    links = _extract_press_links(body, base_url=url)
    out: list[ExaResult] = []
    for link in links[:num_results]:
        title = link.text or _title_from_url(link.href)
        out.append(ExaResult(
            title=title,
            url=link.href,
            published_at=_parse_date_from_text(f"{title} {link.href}"),
            excerpt=f"Official issuer IR/news link discovered from {url}",
            score=None,
            raw={"source_url": url, "kind": "ir_html_link"},
        ))
    return out


def _get_text(url: str, *, client: httpx.Client, user_agent: str) -> str:
    resp = client.get(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "application/rss+xml, application/atom+xml, text/html, */*",
        },
    )
    if resp.status_code != 200:
        raise IrError(f"IR fetch failed for {url}: {resp.status_code}")
    return resp.text


def _parse_feed(root: ET.Element, *, source_url: str) -> list[ExaResult]:
    out: list[ExaResult] = []
    for item in root.findall(".//item"):
        title = _child_text(item, "title")
        link = _child_text(item, "link")
        if not title or not link:
            continue
        out.append(ExaResult(
            title=title,
            url=link,
            published_at=_parse_dt(_child_text(item, "pubDate") or _child_text(item, "date")),
            excerpt=_child_text(item, "description") or "",
            score=None,
            raw={"source_url": source_url, "kind": "rss_item"},
        ))

    # Atom feeds use namespaces; compare local names so issuer feeds with
    # different namespace prefixes still parse.
    for entry in [e for e in root.iter() if _local_name(e.tag) == "entry"]:
        title = _child_text_any(entry, "title")
        link = _atom_link(entry)
        if not title or not link:
            continue
        out.append(ExaResult(
            title=title,
            url=link,
            published_at=_parse_dt(
                _child_text_any(entry, "published") or _child_text_any(entry, "updated")
            ),
            excerpt=_child_text_any(entry, "summary") or _child_text_any(entry, "content") or "",
            score=None,
            raw={"source_url": source_url, "kind": "atom_entry"},
        ))
    return _dedupe(out)


def _extract_press_links(html: str, *, base_url: str) -> list[_Link]:
    parser = _AnchorParser(base_url)
    parser.feed(html)
    seen: set[str] = set()
    out: list[_Link] = []
    for link in parser.links:
        href = _clean_url(link.href)
        if not href or href in seen:
            continue
        if not _looks_like_press_link(href, link.text):
            continue
        seen.add(href)
        out.append(_Link(href=href, text=_clean_text(link.text)))
    return out


class _AnchorParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.links: list[_Link] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if not href:
            return
        self._href = urljoin(self.base_url, href)
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href:
            self.links.append(_Link(href=self._href, text=" ".join(self._text)))
            self._href = None
            self._text = []


def _child_text(elem: ET.Element, name: str) -> str:
    child = elem.find(name)
    return (child.text or "").strip() if child is not None and child.text else ""


def _child_text_any(elem: ET.Element, local_name: str) -> str:
    for child in elem:
        if _local_name(child.tag) == local_name and child.text:
            return child.text.strip()
    return ""


def _atom_link(entry: ET.Element) -> str:
    for child in entry:
        if _local_name(child.tag) != "link":
            continue
        href = child.attrib.get("href")
        rel = child.attrib.get("rel", "alternate")
        if href and rel in {"alternate", ""}:
            return href.strip()
    return ""


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_date_from_text(text: str) -> datetime | None:
    # Conservative date detection for common /2026/05/30/ URL paths or titles.
    import re

    m = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", text)
    if not m:
        return None
    year, month, day = (int(part) for part in m.groups())
    try:
        return datetime(year, month, day)
    except ValueError:
        return None


def _looks_like_press_link(url: str, text: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    haystack = f"{parsed.path} {parsed.query} {text}".lower()
    return any(k in haystack for k in PRESS_LINK_KEYWORDS)


def _clean_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return ""
    return parsed._replace(fragment="").geturl()


def _clean_text(text: str) -> str:
    return " ".join((text or "").split())[:220]


def _title_from_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/").split("/")[-1]
    return path.replace("-", " ").replace("_", " ").strip().title() or url


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _dedupe(results: list[ExaResult]) -> list[ExaResult]:
    seen: set[str] = set()
    out: list[ExaResult] = []
    for result in results:
        key = result.url.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(result)
    return out
