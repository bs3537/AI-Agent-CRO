"""Poll orchestration.

For each per_holding bucket × each holding, build a query, hit Exa (or load
from fixture), tag results, persist. For each sector bucket, run once with no
ticker filter. Exa failures are recorded as failed poll rows and the cycle
continues — PLAN.MD §6 says "Exa down → skip ingestion cycle, don't crash."
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import settings
from ..portfolio.joined import latest_joined
from ..portfolio.schema import Holding
from .buckets import Bucket, load_buckets
from .exa_client import ExaError, ExaResult, load_response_file, search as exa_search
from .query import entity_terms, literature_query, per_holding_query, sector_query
from .source_tiers import source_label, source_tier
from .store import init_news_schema, save_article, save_poll_record
from .tagger import match_tickers, tag_text

log = logging.getLogger("sma_monitor.news.pipeline")

# Signature shared by live + fixture providers — let the pipeline swap
# them without knowing which source is in use.
Provider = Callable[[str, int, "datetime | None"], list[ExaResult]]


# Run one full poll cycle across (holdings × per_holding buckets) + sector
# buckets. skip_bucket_ids is the Phase 6 cost-cascade hook — pass the
# ids to drop (10/11 at 85%, 12 at 95%) when budget pressure is on.
def poll(
    *,
    api_key: str | None,
    from_file: Path | None = None,
    filter_ticker: str | None = None,
    filter_bucket: int | None = None,
    num_results: int = 5,
    lookback_hours: int = 24,
    skip_bucket_ids: set[int] | None = None,
) -> dict:
    """Run one poll cycle. Returns a summary dict for logging.

    skip_bucket_ids: Phase 6 degrade cascade — when budget pressure is on,
    the orchestrator passes the bucket ids to drop (10/11 at 85%, 12 at 95%).
    """
    init_news_schema()
    holdings, missing, _ = latest_joined()
    if not holdings:
        log.warning("no_holdings_to_poll")
        return {"holdings": 0, "queries": 0, "articles_new": 0}

    if filter_ticker:
        ft = filter_ticker.upper()
        holdings = [h for h in holdings if h.ticker == ft]

    all_buckets = load_buckets()
    if filter_bucket is not None:
        buckets = {filter_bucket: all_buckets[filter_bucket]} if filter_bucket in all_buckets else {}
    else:
        buckets = all_buckets
    if skip_bucket_ids:
        buckets = {bid: b for bid, b in buckets.items() if bid not in skip_bucket_ids}

    if not buckets or not holdings:
        log.warning("poll_filtered_to_empty",
                    extra={"holdings": len(holdings), "buckets": len(buckets)})
        return {"holdings": len(holdings), "queries": 0, "articles_new": 0}

    holdings_entities = {h.ticker: entity_terms(h) for h in holdings}
    provider = _make_provider(api_key, from_file)
    since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    queries = 0
    articles_new = 0
    for b in sorted(buckets.values(), key=lambda b: b.id):
        if b.scope == "sector":
            # Skip sector buckets when the user filtered to a single ticker —
            # sector queries aren't ticker-specific.
            if filter_ticker:
                continue
            queries += 1
            articles_new += _run_one_query(
                bucket=b, holding=None, num_results=num_results,
                provider=provider, since=since,
                holdings_entities=holdings_entities, all_buckets=all_buckets,
            )
        else:
            for h in holdings:
                queries += 1
                articles_new += _run_one_query(
                    bucket=b, holding=h, num_results=num_results,
                    provider=provider, since=since,
                    holdings_entities=holdings_entities, all_buckets=all_buckets,
                )

    return {
        "holdings": len(holdings),
        "missing_sidecars": missing,
        "queries": queries,
        "articles_new": articles_new,
        "skipped_bucket_ids": sorted(skip_bucket_ids or []),
    }


# Pick the right search provider, in priority order:
#   1. fixture replay when from_file is set (offline);
#   2. Brave when BRAVE_SEARCH_API_KEY is set (W2 — the primary source);
#   3. Exa when EXA_API_KEY is set (legacy fallback);
#   4. otherwise error loudly.
# `api_key` is the Exa key passed by callers; Brave/Scite/FMP keys are read
# from settings so the existing poll() signature stays unchanged.
def _make_provider(api_key: str | None, fixture: Path | None) -> Provider:
    if fixture:
        cached = load_response_file(fixture)

        def provider(_q: str, n: int, _since: datetime | None) -> list[ExaResult]:
            return cached[:n]

        return provider

    if settings.brave_search_api_key:
        from .brave_client import search as brave_search

        def brave_provider(q: str, n: int, since: datetime | None) -> list[ExaResult]:
            return brave_search(q, api_key=settings.brave_search_api_key,  # type: ignore[arg-type]
                                num_results=n, start_published_date=since)

        return brave_provider

    if api_key:
        def exa_provider(q: str, n: int, since: datetime | None) -> list[ExaResult]:
            return exa_search(q, api_key=api_key, num_results=n, start_published_date=since)

        return exa_provider

    raise RuntimeError(
        "No news source configured: set BRAVE_SEARCH_API_KEY (preferred) or "
        "EXA_API_KEY in .env, or pass --from-file for an offline fixture."
    )


# Execute one (bucket, holding) or (bucket, None) query: build the query
# string, call the provider, persist results, and record the poll row.
# Returns the count of newly-discovered articles.
def _run_one_query(
    *,
    bucket: Bucket,
    holding: Holding | None,
    num_results: int,
    provider: Provider,
    since: datetime,
    holdings_entities: dict[str, list[str]],
    all_buckets: dict[int, Bucket],
) -> int:
    if holding is None:
        query = sector_query(bucket)
        ticker = None
    else:
        query = per_holding_query(holding, bucket)
        ticker = holding.ticker
    started_at = datetime.now(timezone.utc)
    try:
        results = provider(query, num_results, since)
    except (ExaError, RuntimeError) as e:
        log.error("news_query_failed",
                  extra={"ticker": ticker, "bucket_id": bucket.id, "err": str(e)})
        save_poll_record(
            ticker=ticker, bucket_id=bucket.id, query_text=query,
            started_at=started_at, ended_at=datetime.now(timezone.utc),
            n_results=0, n_new=0, status="error",
        )
        return 0

    fetched_at = datetime.now(timezone.utc)
    n_new = 0
    for res in results:
        if _store_one(
            res, fetched_at=fetched_at,
            holdings_entities=holdings_entities, all_buckets=all_buckets,
            query_ticker=ticker, query_bucket_id=bucket.id,
        ):
            n_new += 1

    save_poll_record(
        ticker=ticker, bucket_id=bucket.id, query_text=query,
        started_at=started_at, ended_at=fetched_at,
        n_results=len(results), n_new=n_new, status="ok",
    )
    log.info("news_query_ok",
             extra={"ticker": ticker, "bucket_id": bucket.id,
                    "n_results": len(results), "n_new": n_new})
    return n_new


# Persist one Exa result: ticker-match, bucket-tag, and upsert via the store.
# Includes two safe fallbacks — if matching returns nothing, fall back to the
# query's originating ticker/bucket so the article still flows downstream.
def _store_one(
    res: ExaResult,
    *,
    fetched_at: datetime,
    holdings_entities: dict[str, list[str]],
    all_buckets: dict[int, Bucket],
    query_ticker: str | None,
    query_bucket_id: int | None,
    force_bucket_id: int | None = None,
) -> bool:
    if not res.url or not res.title:
        return False
    text_for_match = f"{res.title} {res.excerpt}"

    matched = match_tickers(text_for_match, holdings_entities)
    # Fallback: if the tagger found no tickers but the query was per-holding,
    # the originating ticker is the best guess.
    if not matched and query_ticker:
        matched = [query_ticker]

    bucket_tags = [(t.bucket_id, t.confidence) for t in tag_text(text_for_match, all_buckets)]
    # Fallback: query's bucket counts as a low-confidence tag if the keyword
    # tagger missed (article still flows downstream, just flagged as weak).
    if not bucket_tags and query_bucket_id:
        bucket_tags = [(query_bucket_id, 0.1)]
    # force_bucket_id (W2 literature): the source guarantees this bucket
    # (e.g. Scite → #10), so ensure it's tagged even if the keyword tagger
    # latched onto a different bucket from the abstract.
    if force_bucket_id is not None and not any(bid == force_bucket_id for bid, _ in bucket_tags):
        bucket_tags.append((force_bucket_id, 0.6))

    _, is_new = save_article(
        url=res.url,
        title=res.title,
        excerpt=_lede(res.excerpt, 800),  # keep more text than the lede for downstream
        source=source_label(res.url),
        source_tier=source_tier(res.url),
        published_at=res.published_at,
        fetched_at=fetched_at,
        tickers=matched,
        bucket_tags=bucket_tags,
        raw_json=json.dumps(res.raw),
    )
    return is_new


# Run a literature poll (W2 / bucket #10): for each holding, search Scite on
# its drug/indication terms and store the hits as bucket-#10 articles. Mirrors
# poll()'s structure but on a single bucket. Skips gracefully (RuntimeError)
# when no Scite key and no fixture is available, so the daily cycle is safe.
def poll_literature(
    *,
    api_key: str | None,
    from_file: Path | None = None,
    filter_ticker: str | None = None,
    num_results: int = 5,
) -> dict:
    """Scite literature poll → bucket #10. Returns a summary dict."""
    init_news_schema()
    holdings, missing, _ = latest_joined()
    if filter_ticker:
        ft = filter_ticker.upper()
        holdings = [h for h in holdings if h.ticker == ft]
    if not holdings:
        log.warning("no_holdings_to_poll_literature")
        return {"holdings": 0, "queries": 0, "articles_new": 0}

    all_buckets = load_buckets()
    holdings_entities = {h.ticker: entity_terms(h) for h in holdings}
    provider = _make_scite_provider(api_key, from_file)

    queries = articles_new = 0
    for h in holdings:
        term = literature_query(h)
        if not term:
            continue
        queries += 1
        started_at = datetime.now(timezone.utc)
        try:
            results = provider(term, num_results)
        except RuntimeError as e:
            log.error("literature_query_failed", extra={"ticker": h.ticker, "err": str(e)})
            save_poll_record(ticker=h.ticker, bucket_id=10, query_text=term,
                             started_at=started_at, ended_at=datetime.now(timezone.utc),
                             n_results=0, n_new=0, status="error")
            continue
        fetched_at = datetime.now(timezone.utc)
        n_new = 0
        for res in results:
            if _store_one(res, fetched_at=fetched_at, holdings_entities=holdings_entities,
                          all_buckets=all_buckets, query_ticker=h.ticker,
                          query_bucket_id=10, force_bucket_id=10):
                n_new += 1
        save_poll_record(ticker=h.ticker, bucket_id=10, query_text=term,
                         started_at=started_at, ended_at=fetched_at,
                         n_results=len(results), n_new=n_new, status="ok")
        articles_new += n_new
        log.info("literature_query_ok",
                 extra={"ticker": h.ticker, "n_results": len(results), "n_new": n_new})

    return {"holdings": len(holdings), "missing_sidecars": missing,
            "queries": queries, "articles_new": articles_new}


# Pick the Scite provider: fixture replay when from_file is set, else live
# Scite when the key is present, else raise (literature poll skipped).
def _make_scite_provider(api_key: str | None, fixture: Path | None):
    if fixture:
        from .scite_client import load_response_file as scite_load
        cached = scite_load(fixture)

        def provider(_term: str, n: int) -> list[ExaResult]:
            return cached[:n]

        return provider
    if not api_key:
        raise RuntimeError(
            "SCITE_API_KEY is not set and no --from-file fixture was provided; "
            "literature (bucket #10) poll skipped."
        )
    from .scite_client import search as scite_search

    def live_provider(term: str, n: int) -> list[ExaResult]:
        return scite_search(term, api_key=api_key, num_results=n)

    return live_provider


# Extract the lede (first sentence) from a longer excerpt, with a hard
# character cap. Used to keep stored excerpts bounded.
def _lede(text: str, max_len: int = 200) -> str:
    """First sentence or first N chars — used for article_event_id and excerpt."""
    text = (text or "").strip()
    if not text:
        return ""
    for stop in (". ", "! ", "? "):
        idx = text.find(stop, 20)
        if 20 < idx <= max_len:
            return text[: idx + 1].strip()
    return text[:max_len].strip()
