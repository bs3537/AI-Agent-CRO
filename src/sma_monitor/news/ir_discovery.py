"""Discover official investor-relations URLs for sidecars."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

from ..config import settings
from .brave_web_client import BraveWebResult
from .brave_web_client import search as brave_web_search

log = logging.getLogger("sma_monitor.news.ir_discovery")

EXCLUDED_HOST_PARTS = (
    "businesswire.com",
    "globenewswire.com",
    "prnewswire.com",
    "accesswire.com",
    "theglobeandmail.com",
    "insidermonkey.com",
    "nasdaq.com",
    "yahoo.com",
    "finance.yahoo.com",
    "marketwatch.com",
    "seekingalpha.com",
    "benzinga.com",
    "fool.com",
    "zacks.com",
    "stockanalysis.com",
    "stocktitan.net",
    "tipranks.com",
    "streetinsider.com",
    "gurufocus.com",
    "marketbeat.com",
    "marketscreener.com",
    "morningstar.com",
    "redchip.com",
    "sec.gov",
    "otcmarkets.com",
    "wikipedia.org",
    "reddit.com",
)

IR_HOST_PREFIXES = ("ir.", "investor.", "investors.")
PRESS_PATH_MARKERS = (
    "press-releases",
    "news-releases",
    "news-events",
    "news-and-events",
    "news-and-presentations",
    "newsroom",
    "news/default.aspx",
    "/press/",
)
IR_PATH_MARKERS = ("investor-relations", "investor-hub", "/investors", "/investor")
BAD_PATH_MARKERS = ("sec-filings", "sec-filing", "/filings/", "ownership", "fileadmin", ".pdf")


@dataclass(frozen=True)
class IrDiscoveryResult:
    ticker: str
    ir_url: str | None = None
    press_releases_url: str | None = None
    press_release_rss_url: str | None = None
    source_url: str | None = None
    status: str = "not_found"
    reason: str | None = None

    @property
    def found_any(self) -> bool:
        return bool(self.ir_url or self.press_releases_url or self.press_release_rss_url)


def discover_ir_urls(
    *,
    ticker: str,
    company_name: str | None = None,
    api_key: str | None = None,
    max_results: int = 10,
) -> IrDiscoveryResult:
    """Find and validate issuer IR URLs. Returns empty result on failure."""
    ticker = ticker.strip().upper()
    api_key = api_key if api_key is not None else settings.brave_search_api_key
    if not api_key:
        return IrDiscoveryResult(ticker=ticker, status="skipped", reason="brave_key_missing")

    queries = _queries(ticker, company_name)
    seen: set[str] = set()
    results: list[BraveWebResult] = list(_guessed_results(ticker, company_name))
    for q in queries:
        try:
            for row in brave_web_search(q, api_key=api_key, num_results=max_results):
                norm = _normalize_url(row.url)
                if norm and norm not in seen:
                    seen.add(norm)
                    results.append(row)
        except Exception as e:
            log.warning("ir_discovery_search_failed",
                        extra={"ticker": ticker, "query": q, "err": str(e)[:240]})

    candidates = sorted(
        (_candidate(row, ticker=ticker, company_name=company_name) for row in results),
        key=lambda c: c.score,
        reverse=True,
    )
    candidates = [c for c in candidates if c.score > 0]
    if not candidates:
        return IrDiscoveryResult(ticker=ticker, status="not_found", reason="no_official_candidate")

    with httpx.Client(timeout=12.0, follow_redirects=True,
                      headers={"User-Agent": "AI-CRO IR discovery"}) as client:
        for c in candidates:
            checked = _validate_candidate(c, client=client)
            if checked is None:
                continue
            press_url = (
                _press_landing_url(checked)
                if _is_press_url(checked)
                else _discover_press_page(checked, client=client)
            )
            rss = _discover_rss(press_url or checked, client=client)
            ir_url = _landing_url(checked)
            return IrDiscoveryResult(
                ticker=ticker,
                ir_url=ir_url,
                press_releases_url=press_url,
                press_release_rss_url=rss,
                source_url=c.url,
                status="ok",
            )

    return IrDiscoveryResult(ticker=ticker, status="not_found", reason="validation_failed")


def _queries(ticker: str, company_name: str | None) -> list[str]:
    entity = f'"{company_name}" {ticker}' if company_name else ticker
    return [
        f"{entity} investor relations press releases",
        f"{entity} investor relations news releases",
        f"{entity} official investor relations",
    ]


def _guessed_results(ticker: str, company_name: str | None) -> list[BraveWebResult]:
    tokens = _company_tokens(company_name)
    if not tokens:
        return []
    stems = {tokens[0]}
    if len(tokens) >= 2:
        stems.add(tokens[0] + tokens[1])
    out: list[BraveWebResult] = []
    for stem in sorted(stems):
        for host in (f"ir.{stem}.com", f"investors.{stem}.com", f"investor.{stem}.com"):
            out.append(BraveWebResult(
                title=f"{company_name or ticker} investor relations",
                url=f"https://{host}/",
                description=f"Guessed official investor relations host for {ticker}.",
                raw={"source": "guess"},
            ))
        for path in ("investor-hub", "investors", "investor-relations"):
            out.append(BraveWebResult(
                title=f"{company_name or ticker} investor relations",
                url=f"https://{stem}.com/{path}",
                description=f"Guessed official investor relations path for {ticker}.",
                raw={"source": "guess"},
            ))
    return out


@dataclass(frozen=True)
class _Candidate:
    url: str
    title: str
    description: str
    score: int


def _candidate(row: BraveWebResult, *, ticker: str, company_name: str | None) -> _Candidate:
    url = _normalize_url(row.url)
    if not url:
        return _Candidate(row.url, row.title, row.description, 0)
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    if any(part in host for part in EXCLUDED_HOST_PARTS):
        return _Candidate(url, row.title, row.description, 0)
    if any(marker in parsed.path.lower() for marker in BAD_PATH_MARKERS):
        return _Candidate(url, row.title, row.description, 0)

    haystack = f"{host} {parsed.path} {row.title} {row.description}".lower()
    tokens = _company_tokens(company_name)
    company_host_match = bool(tokens and any(t in host for t in tokens))
    company_text_match = bool(tokens and any(t in haystack for t in tokens[:3]))
    issuer_host = host.startswith(IR_HOST_PREFIXES) or "investor" in host
    ticker_host_match = len(ticker) >= 4 and _compact(ticker) in _compact(host)
    if tokens and not (
        company_host_match
        or ticker_host_match
        or (issuer_host and company_text_match)
    ):
        return _Candidate(url, row.title, row.description, 0)
    if not tokens and not (issuer_host or ticker_host_match):
        return _Candidate(url, row.title, row.description, 0)

    score = 0
    if host.startswith(IR_HOST_PREFIXES):
        score += 40
    if any(marker in parsed.path.lower() for marker in PRESS_PATH_MARKERS):
        score += 35
    if any(marker in parsed.path.lower() for marker in IR_PATH_MARKERS):
        score += 20
    if "investor" in haystack:
        score += 15
    if "press release" in haystack or "news release" in haystack:
        score += 15
    if ticker.lower() in haystack:
        score += 8

    if company_host_match:
        score += 20
    elif company_text_match:
        score += 8

    return _Candidate(url, row.title, row.description, score)


def _validate_candidate(url: _Candidate, *, client: httpx.Client) -> str | None:
    try:
        resp = client.get(url.url)
    except Exception:
        return None
    if resp.status_code >= 400:
        return None
    final = _normalize_url(str(resp.url))
    if not final:
        return None
    parsed = urlparse(final)
    if any(part in parsed.netloc.lower() for part in EXCLUDED_HOST_PARTS):
        return None
    if any(marker in parsed.path.lower() for marker in BAD_PATH_MARKERS):
        return None
    return final


def _discover_rss(page_url: str, *, client: httpx.Client) -> str | None:
    try:
        resp = client.get(page_url)
    except Exception:
        return None
    if resp.status_code >= 400:
        return None
    parser = _RssParser(page_url)
    try:
        parser.feed(resp.text[:300_000])
    except Exception:
        return None
    for href in parser.hrefs:
        if _validate_rss(href, client=client):
            return href
    return None


def _discover_press_page(page_url: str, *, client: httpx.Client) -> str | None:
    try:
        resp = client.get(page_url)
    except Exception:
        return None
    if resp.status_code >= 400:
        return None
    parser = _PressLinkParser(page_url)
    try:
        parser.feed(resp.text[:300_000])
    except Exception:
        return None
    candidates = sorted(
        {_press_landing_url(h) for h in parser.hrefs if _is_press_url(h)},
        key=_press_link_score,
        reverse=True,
    )
    for href in candidates:
        normalized = _normalize_url(href)
        if not normalized:
            continue
        candidate = _Candidate(normalized, "press releases", "", 1)
        final = _validate_candidate(candidate, client=client)
        if final:
            return _press_landing_url(final)
    return None


def _validate_rss(url: str, *, client: httpx.Client) -> bool:
    try:
        resp = client.get(url, headers={"Accept": "application/rss+xml, application/atom+xml, */*"})
    except Exception:
        return False
    if resp.status_code >= 400:
        return False
    head = resp.text[:500].lower()
    return "<rss" in head or "<feed" in head


class _RssParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k.lower(): v for k, v in attrs if v}
        href = attr.get("href")
        if not href:
            return
        text = " ".join([attr.get("type", ""), attr.get("title", ""), href]).lower()
        if "rss" not in text and "atom" not in text:
            return
        self.hrefs.append(urljoin(self.base_url, href))


class _PressLinkParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.hrefs: list[str] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attr = {k.lower(): v for k, v in attrs if v}
        self._href = attr.get("href")
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._href:
            return
        haystack = f"{self._href} {' '.join(self._text)}".lower()
        if any(marker in haystack for marker in PRESS_PATH_MARKERS):
            self.hrefs.append(urljoin(self.base_url, self._href))
        self._href = None
        self._text = []


def _landing_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith(IR_HOST_PREFIXES):
        return parsed._replace(path="/", query="", fragment="").geturl()

    path = parsed.path
    for marker in IR_PATH_MARKERS:
        idx = path.lower().find(marker.strip("/"))
        if idx >= 0:
            prefix = path[: idx + len(marker.strip("/"))]
            if not prefix.startswith("/"):
                prefix = "/" + prefix
            return parsed._replace(path=prefix.rstrip("/") or "/", query="", fragment="").geturl()
    return parsed._replace(path="/", query="", fragment="").geturl()


def _is_press_url(url: str) -> bool:
    return any(marker in urlparse(url).path.lower() for marker in PRESS_PATH_MARKERS)


def _press_landing_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path
    lower = path.lower()
    for marker in PRESS_PATH_MARKERS:
        clean = marker.strip("/")
        idx = lower.find(clean)
        if idx < 0:
            continue
        prefix = path[: idx + len(clean)]
        if not prefix.startswith("/"):
            prefix = "/" + prefix
        return parsed._replace(path=prefix.rstrip("/") or "/", query="", fragment="").geturl()
    return parsed._replace(query="", fragment="").geturl()


def _press_link_score(url: str) -> int:
    path = urlparse(url).path.lower()
    if "press-releases" in path or "news-releases" in path:
        return 30
    if "news-events" in path or "news-and-events" in path:
        return 20
    if "newsroom" in path:
        return 10
    return 0


def _normalize_url(url: str) -> str:
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return parsed._replace(fragment="").geturl()


def _company_tokens(company_name: str | None) -> list[str]:
    if not company_name:
        return []
    stop = {
        "inc", "incorporated", "therapeutics", "therapeutic", "pharmaceutical",
        "pharmaceuticals", "biosciences", "biotherapeutics", "group", "holdings",
        "limited", "ltd", "corp", "corporation", "company", "nv", "plc",
    }
    return [
        t
        for t in re.findall(r"[a-z0-9]+", company_name.lower())
        if len(t) >= 4 and t not in stop
    ]


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())
