"""PubMed literature adapter (biomed-literature primary, ahead of Semantic Scholar).

Searches PubMed via NCBI E-utilities and returns articles as ExaResults, so
primary biomedical literature leads for biomed holdings (see news/source_policy:
PubMed / ClinicalTrials.gov / web -> Semantic Scholar). Each hit links to
https://pubmed.ncbi.nlm.nih.gov/{pmid}/ (source_tiers -> tier 3).

Two GETs: esearch (query -> newest PMIDs) then esummary (PMIDs -> metadata).
Auth: none required; an optional NCBI_API_KEY raises the rate limit from 3 to 10
req/s. Offline replay via load_response_file parses a saved esummary JSON.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from .exa_client import ExaResult

# NCBI E-utilities endpoints: esearch maps a query to PMIDs, esummary maps PMIDs
# to article metadata. PubMed article pages are built from the PMID.
PUBMED_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
PUBMED_ARTICLE = "https://pubmed.ncbi.nlm.nih.gov/{pmid}/"


# Exception for any non-200 / unparseable E-utilities response. Caught by the
# caller so a failed query records an error and the cycle continues.
class PubMedError(RuntimeError):
    pass


# Run one PubMed search and return ExaResults. esearch resolves the query to the
# newest `num_results` PMIDs, then esummary fetches their metadata. `api_key` is
# optional (raises NCBI's rate limit). Signature mirrors the other adapters.
def search(
    term: str,
    *,
    api_key: str | None = None,
    num_results: int = 5,
    client: httpx.Client | None = None,
) -> list[ExaResult]:
    """One PubMed search -> list[ExaResult] (biomed-literature primary)."""
    owns = client is None
    client = client or httpx.Client(timeout=30.0)
    try:
        base: dict[str, Any] = {"db": "pubmed", "retmode": "json"}
        if api_key:
            base["api_key"] = api_key
        es = client.get(
            PUBMED_ESEARCH,
            params={**base, "term": term, "retmax": num_results, "sort": "date"},
        )
        if es.status_code != 200:
            raise PubMedError(f"PubMed esearch failed: {es.status_code} {es.text[:200]}")
        ids = (es.json().get("esearchresult") or {}).get("idlist") or []
        if not ids:
            return []
        su = client.get(PUBMED_ESUMMARY, params={**base, "id": ",".join(ids)})
        if su.status_code != 200:
            raise PubMedError(f"PubMed esummary failed: {su.status_code} {su.text[:200]}")
        return _parse_summary(su.json())
    finally:
        if owns:
            client.close()


# Load a saved PubMed esummary JSON from disk — offline replay.
def load_response_file(path: Path) -> list[ExaResult]:
    return _parse_summary(json.loads(path.read_text()))


# Parse an esummary JSON into ExaResults. result.uids lists the PMIDs in order;
# each result[uid] carries title, journal (source), pubdate, authors, and
# articleids (DOI). Builds the canonical PubMed article URL (-> tier 3); skips
# entries with no title (e.g. error stubs for invalid uids).
def _parse_summary(body: dict[str, Any]) -> list[ExaResult]:
    result = body.get("result") or {}
    uids = result.get("uids") or []
    out: list[ExaResult] = []
    for uid in uids:
        r = result.get(uid) or {}
        title = (r.get("title") or "").strip()
        if not title:
            continue
        source = (r.get("source") or "").strip()
        authors = [a.get("name", "") for a in (r.get("authors") or [])][:3]
        doi = next(
            (a.get("value") for a in (r.get("articleids") or []) if a.get("idtype") == "doi"),
            None,
        )
        excerpt = source + (f". {', '.join(authors)}" if authors else "")
        if doi:
            excerpt += f". doi:{doi}"
        out.append(
            ExaResult(
                title=f"{title} ({source})" if source else title,
                url=PUBMED_ARTICLE.format(pmid=uid),
                published_at=_parse_pubdate(r.get("pubdate")),
                excerpt=excerpt.strip(),
                score=None,
                raw=r,
            )
        )
    return out


# Parse a PubMed pubdate ("2024 May 9", "2024 May", "2024", seasons, ranges)
# into a datetime. Tries the common formats, then falls back to the first
# 4-digit year; returns None when nothing parses.
def _parse_pubdate(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y %b %d", "%Y %b", "%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    m = re.search(r"\d{4}", s)
    return datetime(int(m.group()), 1, 1) if m else None
