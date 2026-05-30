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
from .source_policy import literature_order
from .source_tiers import source_label, source_tier
from .store import init_news_schema, save_article, save_poll_record
from .tagger import match_tickers, tag_text

log = logging.getLogger("sma_monitor.news.pipeline")

# Signature shared by live + fixture providers — let the pipeline swap
# them without knowing which source is in use.
Provider = Callable[[str, int, "datetime | None"], list[ExaResult]]

# Targeted morning news queries are meant to answer "did anything fresh happen?"
# rather than exhaustively classify the company. If the title/excerpt does not
# match any factor bucket terms, keep it scorable under strategic/corporate
# news with low confidence instead of dropping the article from the LLM path.
COMPANY_NEWS_FALLBACK_BUCKET_ID = 9


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


def poll_company_news(
    *,
    api_key: str | None,
    from_file: Path | None = None,
    filter_ticker: str | None = None,
    num_results: int = 5,
    lookback_hours: int = 30,
) -> dict:
    """Target fresh company/Yahoo news for the morning smart-recompute gate.

    This is deliberately narrower than the full factor-bucket poll. It runs two
    high-signal queries per holding: one aimed at issuer/IR press releases and
    one at Yahoo Finance news. Newly stored rows still flow through the normal
    article_tickers/article_buckets/scorer path.
    """
    init_news_schema()
    holdings, missing, _ = latest_joined()
    if filter_ticker:
        ft = filter_ticker.upper()
        holdings = [h for h in holdings if h.ticker == ft]
    if not holdings:
        log.warning("no_holdings_to_poll_company_news")
        return {
            "holdings": 0,
            "queries": 0,
            "articles_new": 0,
            "by_ticker": {},
        }

    all_buckets = load_buckets()
    holdings_entities = {h.ticker: entity_terms(h) for h in holdings}
    try:
        provider = _make_provider(api_key, from_file)
        provider_error = None
    except RuntimeError as e:
        provider = None
        provider_error = e
    since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    queries = 0
    articles_new = 0
    by_ticker: dict[str, dict[str, int]] = {}
    for h in holdings:
        ticker_new = 0
        ticker_results = 0
        ticker_errors = 0
        exact = _run_exact_ir_sources(
            holding=h,
            num_results=num_results,
            holdings_entities=holdings_entities,
            all_buckets=all_buckets,
        )
        queries += exact["queries"]
        ticker_new += exact["new"]
        ticker_results += exact["results"]
        ticker_errors += exact["errors"]

        for source_hint, query in _company_news_queries(
            h,
            skip_company_ir=exact["configured"] and exact["errors"] == 0 and exact["results"] > 0,
        ):
            queries += 1
            started_at = datetime.now(timezone.utc)
            query_text = f"{source_hint}: {query}"
            if provider is None:
                ticker_errors += 1
                save_poll_record(
                    ticker=h.ticker,
                    bucket_id=COMPANY_NEWS_FALLBACK_BUCKET_ID,
                    query_text=query_text,
                    started_at=started_at,
                    ended_at=datetime.now(timezone.utc),
                    n_results=0,
                    n_new=0,
                    status="error",
                )
                continue
            try:
                results = provider(query, num_results, since)
            except (ExaError, RuntimeError) as e:
                ticker_errors += 1
                log.error("company_news_query_failed",
                          extra={"ticker": h.ticker, "source_hint": source_hint,
                                 "err": str(e)})
                save_poll_record(
                    ticker=h.ticker,
                    bucket_id=COMPANY_NEWS_FALLBACK_BUCKET_ID,
                    query_text=query_text,
                    started_at=started_at,
                    ended_at=datetime.now(timezone.utc),
                    n_results=0,
                    n_new=0,
                    status="error",
                )
                continue

            fetched_at = datetime.now(timezone.utc)
            n_new = 0
            for res in results:
                if _store_one(
                    res,
                    fetched_at=fetched_at,
                    holdings_entities=holdings_entities,
                    all_buckets=all_buckets,
                    query_ticker=h.ticker,
                    query_bucket_id=COMPANY_NEWS_FALLBACK_BUCKET_ID,
                ):
                    n_new += 1
            save_poll_record(
                ticker=h.ticker,
                bucket_id=COMPANY_NEWS_FALLBACK_BUCKET_ID,
                query_text=query_text,
                started_at=started_at,
                ended_at=fetched_at,
                n_results=len(results),
                n_new=n_new,
                status="ok",
            )
            ticker_new += n_new
            ticker_results += len(results)
            log.info("company_news_query_ok",
                     extra={"ticker": h.ticker, "source_hint": source_hint,
                            "n_results": len(results), "n_new": n_new})
        by_ticker[h.ticker] = {
            "results": ticker_results,
            "new": ticker_new,
            "errors": ticker_errors,
            "exact_ir_configured": int(exact["configured"]),
        }
        articles_new += ticker_new

    summary = {
        "holdings": len(holdings),
        "missing_sidecars": missing,
        "queries": queries,
        "articles_new": articles_new,
        "by_ticker": by_ticker,
        "lookback_hours": lookback_hours,
    }
    if provider_error is not None:
        summary["search_provider_status"] = "unavailable"
        summary["search_provider_reason"] = str(provider_error)[:240]
    return summary


def _run_exact_ir_sources(
    *,
    holding: Holding,
    num_results: int,
    holdings_entities: dict[str, list[str]],
    all_buckets: dict[int, Bucket],
) -> dict[str, int | bool]:
    from . import ir_client

    if not ir_client.configured(holding):
        return {"configured": False, "queries": 0, "results": 0, "new": 0, "errors": 0}

    query_text = "official_ir:" + ",".join(
        u for u in (holding.press_release_rss_url, holding.press_releases_url, holding.ir_url) if u
    )
    started_at = datetime.now(timezone.utc)
    try:
        results = ir_client.search(holding, num_results=num_results)
    except Exception as e:
        log.warning("company_ir_exact_failed",
                    extra={"ticker": holding.ticker, "err": str(e)[:240]})
        save_poll_record(
            ticker=holding.ticker,
            bucket_id=COMPANY_NEWS_FALLBACK_BUCKET_ID,
            query_text=query_text,
            started_at=started_at,
            ended_at=datetime.now(timezone.utc),
            n_results=0,
            n_new=0,
            status="error",
        )
        return {"configured": True, "queries": 1, "results": 0, "new": 0, "errors": 1}

    fetched_at = datetime.now(timezone.utc)
    n_new = 0
    for res in results:
        if _store_one(
            res,
            fetched_at=fetched_at,
            holdings_entities=holdings_entities,
            all_buckets=all_buckets,
            query_ticker=holding.ticker,
            query_bucket_id=COMPANY_NEWS_FALLBACK_BUCKET_ID,
            source_tier_override=1,
        ):
            n_new += 1
    save_poll_record(
        ticker=holding.ticker,
        bucket_id=COMPANY_NEWS_FALLBACK_BUCKET_ID,
        query_text=query_text,
        started_at=started_at,
        ended_at=fetched_at,
        n_results=len(results),
        n_new=n_new,
        status="ok",
    )
    log.info("company_ir_exact_ok",
             extra={"ticker": holding.ticker, "n_results": len(results), "n_new": n_new})
    return {
        "configured": True,
        "queries": 1,
        "results": len(results),
        "new": n_new,
        "errors": 0,
    }


def _company_news_queries(holding: Holding, *, skip_company_ir: bool = False) -> list[tuple[str, str]]:
    company = holding.company_name or holding.ticker
    entity = f'"{company}" {holding.ticker}' if company != holding.ticker else holding.ticker
    queries = []
    if not skip_company_ir:
        queries.append((
            "company_ir",
            f"{entity} investor relations press release news release",
        ))
    queries.append(
        (
            "yahoo_finance",
            f"site:finance.yahoo.com {entity} stock news",
        ),
    )
    return queries


# Pick the right search provider, in priority order:
#   1. fixture replay when from_file is set (offline);
#   2. Brave when BRAVE_SEARCH_API_KEY is set (W2 — the primary source);
#   3. Exa when EXA_API_KEY is set (legacy fallback);
#   4. otherwise error loudly.
# `api_key` is the Exa key passed by callers; Brave/Semantic Scholar/FMP keys are read
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
    source_tier_override: int | None = None,
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
    # (e.g. Semantic Scholar → #10), so ensure it's tagged even if the keyword tagger
    # latched onto a different bucket from the abstract.
    if force_bucket_id is not None and not any(bid == force_bucket_id for bid, _ in bucket_tags):
        bucket_tags.append((force_bucket_id, 0.6))

    _, is_new = save_article(
        url=res.url,
        title=res.title,
        excerpt=_lede(res.excerpt, 800),  # keep more text than the lede for downstream
        source=source_label(res.url),
        source_tier=source_tier_override if source_tier_override is not None else source_tier(res.url),
        published_at=res.published_at,
        fetched_at=fetched_at,
        tickers=matched,
        bucket_tags=bucket_tags,
        raw_json=json.dumps(res.raw),
    )
    return is_new


# Run a literature poll (bucket #10): for each holding, query the biomed/general
# literature primaries per source_policy (PubMed/ClinicalTrials.gov/web) plus
# Semantic Scholar, storing every hit as a bucket-#10 article. Each source is
# isolated — a flaky one records an error poll row and the cycle goes on.
def poll_literature(
    *,
    api_key: str | None,
    from_file: Path | None = None,
    filter_ticker: str | None = None,
    num_results: int = 5,
) -> dict:
    """Literature poll → bucket #10 (PubMed/ClinicalTrials.gov/web + Semantic
    Scholar, in source_policy order). Returns a summary dict."""
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

    queries = articles_new = 0
    for h in holdings:
        term = literature_query(h)
        if not term:
            continue
        sources = _literature_sources(
            h, s2_key=api_key, brave_key=settings.brave_search_api_key,
            ncbi_key=settings.ncbi_api_key, fixture=from_file,
        )
        for source_name, fetch in sources:
            queries += 1
            started_at = datetime.now(timezone.utc)
            try:
                results = fetch(term, num_results)
            except Exception as e:  # one flaky source must not sink the cycle
                log.error("literature_query_failed",
                          extra={"ticker": h.ticker, "source": source_name, "err": str(e)})
                save_poll_record(ticker=h.ticker, bucket_id=10,
                                 query_text=f"{source_name}: {term}",
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
            save_poll_record(ticker=h.ticker, bucket_id=10,
                             query_text=f"{source_name}: {term}",
                             started_at=started_at, ended_at=fetched_at,
                             n_results=len(results), n_new=n_new, status="ok")
            articles_new += n_new
            log.info("literature_query_ok",
                     extra={"ticker": h.ticker, "source": source_name,
                            "n_results": len(results), "n_new": n_new})

    return {"holdings": len(holdings), "missing_sidecars": missing,
            "queries": queries, "articles_new": articles_new}


# Build the ordered (source_name, fetch) list for a holding's literature poll,
# following source_policy precedence (biomed: PubMed/CT.gov/web → S2; general:
# web → S2) and including only sources whose key is available. PubMed and
# ClinicalTrials.gov are keyless; web needs Brave, Semantic Scholar its key.
# Keys are passed in (not read from settings) so the ordering is unit-testable.
def _literature_sources(h, *, s2_key, brave_key, ncbi_key, fixture):
    # Offline replay: a single Semantic Scholar fixture (tests / --from-file).
    if fixture:
        return [("semantic_scholar", _make_literature_provider(s2_key, fixture))]
    from . import brave_client, clinicaltrials_client, pubmed_client, semantic_scholar_client

    available = {
        "pubmed": lambda term, n: pubmed_client.search(term, api_key=ncbi_key, num_results=n),
        "clinicaltrials_gov": lambda term, n: clinicaltrials_client.search(term, num_results=n),
    }
    if brave_key:
        available["web_search"] = lambda term, n: brave_client.search(
            term, api_key=brave_key, num_results=n)
    if s2_key:
        available["semantic_scholar"] = lambda term, n: semantic_scholar_client.search(
            term, api_key=s2_key, num_results=n)
    return [(name, available[name]) for name in literature_order(h) if name in available]


# Pick the literature provider: fixture replay when from_file is set, else live
# Semantic Scholar when the key is present, else raise (literature poll skipped).
def _make_literature_provider(api_key: str | None, fixture: Path | None):
    if fixture:
        from .semantic_scholar_client import load_response_file as s2_load
        cached = s2_load(fixture)

        def provider(_term: str, n: int) -> list[ExaResult]:
            return cached[:n]

        return provider
    if not api_key:
        raise RuntimeError(
            "SEMANTIC_SCHOLAR_API_KEY is not set and no --from-file fixture was "
            "provided; literature (bucket #10) poll skipped."
        )
    from .semantic_scholar_client import search as s2_search

    def live_provider(term: str, n: int) -> list[ExaResult]:
        return s2_search(term, api_key=api_key, num_results=n)

    return live_provider


# Run a SEC filings poll (financials primary -> bucket #7): for each holding,
# fetch recent EDGAR filings and store them as tier-1 articles. Per-holding
# failures are isolated; the cycle goes on. SEC needs no key — only a User-Agent.
def poll_sec(
    *,
    user_agent: str,
    filter_ticker: str | None = None,
    num_results: int = 5,
    from_file: Path | None = None,
) -> dict:
    """SEC filings poll -> bucket #7 (Capital Structure & Liquidity). Summary dict."""
    from . import sec_client

    init_news_schema()
    holdings, missing, _ = latest_joined()
    if filter_ticker:
        ft = filter_ticker.upper()
        holdings = [h for h in holdings if h.ticker == ft]
    if not holdings:
        log.warning("no_holdings_to_poll_sec")
        return {"holdings": 0, "queries": 0, "articles_new": 0}

    all_buckets = load_buckets()
    holdings_entities = {h.ticker: entity_terms(h) for h in holdings}

    queries = articles_new = 0
    for h in holdings:
        queries += 1
        started_at = datetime.now(timezone.utc)
        try:
            if from_file is not None:
                results = sec_client.load_response_file(from_file, num_results=num_results)
            else:
                results = sec_client.search(h.ticker, user_agent=user_agent, num_results=num_results)
        except Exception as e:  # an EDGAR hiccup on one name must not sink the cycle
            log.error("sec_query_failed", extra={"ticker": h.ticker, "err": str(e)})
            save_poll_record(ticker=h.ticker, bucket_id=7, query_text=f"sec:{h.ticker}",
                             started_at=started_at, ended_at=datetime.now(timezone.utc),
                             n_results=0, n_new=0, status="error")
            continue
        fetched_at = datetime.now(timezone.utc)
        n_new = 0
        for res in results:
            if _store_one(res, fetched_at=fetched_at, holdings_entities=holdings_entities,
                          all_buckets=all_buckets, query_ticker=h.ticker,
                          query_bucket_id=7, force_bucket_id=7):
                n_new += 1
        save_poll_record(ticker=h.ticker, bucket_id=7, query_text=f"sec:{h.ticker}",
                         started_at=started_at, ended_at=fetched_at,
                         n_results=len(results), n_new=n_new, status="ok")
        articles_new += n_new
        log.info("sec_query_ok",
                 extra={"ticker": h.ticker, "n_results": len(results), "n_new": n_new})

    return {"holdings": len(holdings), "missing_sidecars": missing,
            "queries": queries, "articles_new": articles_new}


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
