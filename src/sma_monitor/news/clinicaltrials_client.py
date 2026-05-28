"""ClinicalTrials.gov adapter (biomed-literature primary, ahead of Semantic Scholar).

Searches ClinicalTrials.gov's v2 API for trials matching a holding's drugs/
indications and returns them as ExaResults, so registered-trial status (phase,
recruitment, conditions, sponsor) is a lead biomedical source for biomed holdings
(see news/source_policy). Each hit links to https://clinicaltrials.gov/study/{nct}
(source_tiers -> tier 2).

Auth: none — the v2 API is public and keyless. Offline replay via
load_response_file parses a saved /studies JSON.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from .exa_client import ExaResult

# ClinicalTrials.gov v2 studies endpoint + the public study-page URL pattern.
CTGOV_STUDIES = "https://clinicaltrials.gov/api/v2/studies"
CTGOV_STUDY_URL = "https://clinicaltrials.gov/study/{nct}"


# Exception for any non-200 / unparseable CT.gov response. Caught by the caller
# so a failed query records an error and the cycle continues.
class ClinicalTrialsError(RuntimeError):
    pass


# Run one ClinicalTrials.gov search and return ExaResults, most-recently-updated
# first. Signature mirrors the other source adapters.
def search(
    term: str,
    *,
    num_results: int = 5,
    client: httpx.Client | None = None,
) -> list[ExaResult]:
    """One ClinicalTrials.gov search -> list[ExaResult] (biomed-literature primary)."""
    owns = client is None
    client = client or httpx.Client(timeout=30.0)
    try:
        resp = client.get(
            CTGOV_STUDIES,
            params={
                "query.term": term,
                "format": "json",
                "pageSize": num_results,
                "sort": "LastUpdatePostDate:desc",
            },
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            raise ClinicalTrialsError(
                f"ClinicalTrials.gov search failed: {resp.status_code} {resp.text[:200]}"
            )
        return _parse_response(resp.json())
    finally:
        if owns:
            client.close()


# Load a saved ClinicalTrials.gov /studies JSON from disk — offline replay.
def load_response_file(path: Path) -> list[ExaResult]:
    return _parse_response(json.loads(path.read_text()))


# Parse a /studies response into ExaResults. Pulls the NCT id, brief title,
# phase/status, conditions and brief summary out of each study's protocolSection,
# builds the canonical study URL (-> tier 2), and dates by last-update post.
def _parse_response(body: dict[str, Any]) -> list[ExaResult]:
    out: list[ExaResult] = []
    for study in body.get("studies") or []:
        ps = study.get("protocolSection") or {}
        ident = ps.get("identificationModule") or {}
        nct = (ident.get("nctId") or "").strip()
        if not nct:
            continue
        title = (ident.get("briefTitle") or ident.get("officialTitle") or "").strip()
        status_mod = ps.get("statusModule") or {}
        status = (status_mod.get("overallStatus") or "").strip()
        phases = (ps.get("designModule") or {}).get("phases") or []
        conditions = (ps.get("conditionsModule") or {}).get("conditions") or []
        summary = ((ps.get("descriptionModule") or {}).get("briefSummary") or "").strip()
        tag = " · ".join(t for t in ["/".join(phases), status] if t)
        cond = ", ".join(conditions[:4])
        out.append(
            ExaResult(
                title=f"{title} [{tag}]" if tag else title,
                url=CTGOV_STUDY_URL.format(nct=nct),
                published_at=_parse_date(
                    (status_mod.get("lastUpdatePostDateStruct") or {}).get("date")
                    or (status_mod.get("startDateStruct") or {}).get("date")
                ),
                excerpt=(f"{cond}. {summary}".strip() if cond else summary)[:800],
                score=None,
                raw=study,
            )
        )
    return out


# Parse a CT.gov date ("2024-05-01", "2024-05", "2024") into a datetime, or None.
def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None
