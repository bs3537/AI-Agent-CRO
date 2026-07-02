"""Read-only portfolio context assembly for the dashboard chatbot."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..decision.store import latest_decision, latest_rating, latest_ratings
from ..decision.technicals import technical_state
from ..news.fmp_client import latest_fmp_metrics, latest_price_series
from ..news.store import recent_articles
from ..portfolio.joined import latest_joined
from ..portfolio.uploads import combined_text, list_files
from ..red_team.store import recent_passes
from ..scorer.store import recent_scores

MAX_CONTEXT_CHARS = 42_000
MAX_DETAIL_TICKERS = 6
LIVE_WEB_TERMS = (
    "catalyst",
    "catalysts",
    "latest",
    "recent",
    "news",
    "filing",
    "fda",
    "pdufa",
    "trial",
    "data",
    "approval",
    "earnings",
)


@dataclass
class ContextBundle:
    text: str
    used_tickers: list[str] = field(default_factory=list)
    cited_context: list[dict[str, Any]] = field(default_factory=list)
    data_freshness: dict[str, Any] = field(default_factory=dict)


def build_chat_context(
    *,
    message: str,
    explicit_ticker: str | None = None,
    include_portfolio: bool = True,
) -> ContextBundle:
    holdings, missing, pulled_at = latest_joined()
    by_ticker = {h.ticker: h for h in holdings}
    tickers = _detect_tickers(message, set(by_ticker), explicit_ticker)
    sections: list[str] = []
    cited: list[dict[str, Any]] = []

    if include_portfolio:
        sections.append(_portfolio_overview(holdings, missing, pulled_at))
        cited.append({"type": "dashboard", "label": "current portfolio dashboard"})

    for ticker in tickers[:MAX_DETAIL_TICKERS]:
        h = by_ticker.get(ticker)
        if h is None:
            continue
        sections.append(_ticker_detail(h))
        cited.append({"type": "holding_detail", "ticker": ticker, "label": f"{ticker} saved data"})

    live_web = _live_web_context(message, [by_ticker[t] for t in tickers if t in by_ticker])
    if live_web["text"]:
        sections.append(live_web["text"])
        cited.append({"type": "native_web_search_request", "label": "Codex native web search request"})

    text = "\n\n".join(sections) or "(No portfolio context is currently available.)"
    if len(text) > MAX_CONTEXT_CHARS:
        text = text[:MAX_CONTEXT_CHARS] + "\n\n[context truncated]"
    return ContextBundle(
        text=text,
        used_tickers=tickers,
        cited_context=cited,
        data_freshness={
            "positions_pulled_at": pulled_at.isoformat() if pulled_at else None,
            "missing_sidecars": missing,
            "live_web_search": live_web["status"],
            "live_web_search_at": live_web["searched_at"],
        },
    )


def _detect_tickers(message: str, held: set[str], explicit_ticker: str | None) -> list[str]:
    found: list[str] = []
    if explicit_ticker:
        t = explicit_ticker.strip().upper()
        if t in held:
            found.append(t)
    for token in re.findall(r"\b[A-Z][A-Z0-9.\-]{0,6}\b", message.upper()):
        t = token.strip().upper()
        if t in held and t not in found:
            found.append(t)
    return found


def _portfolio_overview(holdings, missing: list[str], pulled_at) -> str:
    rows = sorted(holdings, key=lambda h: h.pct_nav, reverse=True)
    rating_rows = {r["ticker"]: r for r in latest_ratings()}
    lines = [
        "CURRENT DASHBOARD PORTFOLIO SNAPSHOT",
        f"Positions pulled at: {pulled_at.isoformat() if pulled_at else 'unknown'}",
        f"Current monitored holdings: {len(rows)}",
    ]
    if missing:
        lines.append(f"Positions missing sidecars: {', '.join(missing[:30])}")
    lines.append("Holdings sorted by % NAV:")
    for h in rows:
        rating = rating_rows.get(h.ticker)
        grade = rating["grade"] if rating else "none"
        action = rating["action"] if rating else "none"
        conf = f"{float(rating['confidence']) * 100:.0f}%" if rating else "n/a"
        model = rating["model_used"] if rating else "n/a"
        decided = rating["decided_at"] if rating else "n/a"
        pnl = _fmt_pnl(h.market_value, h.cost_basis)
        tech = _rating_technical_summary(rating)
        lines.append(
            f"- {h.ticker}: {h.pct_nav * 100:.1f}% NAV, {action.upper()} {grade}, "
            f"conf {conf}, P&L {pnl}, {tech}, model {model}, computed {decided}; "
            f"thesis: {_one_line(h.thesis, 220)}"
        )
    return "\n".join(lines)


def _ticker_detail(h) -> str:
    lines = [
        f"DETAILED SAVED DATA — {h.ticker}",
        f"Company: {h.company_name or 'unknown'}",
        f"Stage: {h.stage}; conviction tier {h.conviction_tier}; weight {h.pct_nav * 100:.2f}% NAV",
        f"Open P&L: {_fmt_pnl(h.market_value, h.cost_basis)}",
        f"Technical: {_technical_summary(h.ticker)}",
        "",
        "Current thesis:",
        _block(h.thesis, 2_400),
    ]

    docs = combined_text(h.ticker, max_chars=4_000)
    files = list_files(h.ticker)
    lines.append("")
    lines.append(f"Uploaded thesis docs ({len(files)} files):")
    lines.append(_block(docs or "(none)", 4_000))

    rating = latest_rating(h.ticker)
    if rating:
        lines.append("")
        lines.append("Latest CRO rating:")
        lines.append(_row_json_line(rating, [
            "grade", "action", "attention_state", "risk_score", "risk_components",
            "technical_state", "deterministic_grade", "llm_grade", "final_grade",
            "note", "drivers", "confidence", "model_used", "compute_source", "decided_at",
        ]))

    decision = latest_decision(h.ticker)
    if decision:
        lines.append("")
        lines.append("Latest legacy thesis-drift decision:")
        lines.append(_row_json_line(decision, [
            "verdict", "color", "note", "drivers", "confidence",
            "model_used", "compute_source", "decided_at",
        ]))

    fmp = latest_fmp_metrics(h.ticker)
    lines.append("")
    lines.append("Latest cached financial metrics:")
    lines.append(json.dumps(fmp or {}, indent=2, default=str)[:3_000])

    scores = recent_scores(ticker=h.ticker, limit=12)
    lines.append("")
    lines.append(f"Recent scored evidence ({len(scores)} shown):")
    for s in scores:
        lines.append(
            f"- composite {s['composite']:.1f} bucket {s['primary_bucket_id']} "
            f"{s['threshold_band']}: {_one_line(s['title'] or '', 120)}; "
            f"rationale: {_one_line(s['rationale'] or '', 240)}"
        )

    bears = recent_passes(ticker=h.ticker, limit=8)
    lines.append("")
    lines.append(f"Recent red-team bear cases ({len(bears)} shown):")
    for b in bears:
        lines.append(
            f"- severity {b['severity_of_concern']}/5: {_one_line(b['title'] or '', 120)}; "
            f"bear: {_one_line(b['bearish_thesis'] or '', 260)}; "
            f"invalidator: {_one_line(b['invalidator'] or '', 160)}"
        )

    articles = recent_articles(ticker=h.ticker, limit=12)
    lines.append("")
    lines.append(f"Recent stored articles/news ({len(articles)} shown):")
    for a in articles:
        lines.append(
            f"- {a['published_at'] or a['fetched_at']}: {_one_line(a['title'] or '', 140)} "
            f"({a['source'] or 'unknown'}); {_one_line(a['excerpt'] or '', 220)}"
        )
    return "\n".join(lines)


def _technical_summary(ticker: str) -> str:
    closes = latest_price_series(ticker)
    if not closes:
        return "technical no_price_data"
    tech = technical_state(closes)
    pct = (
        f"{tech.price_vs_ema20_pct * 100:+.1f}%"
        if tech.price_vs_ema20_pct is not None
        else "n/a"
    )
    return f"technical {tech.technical_state}, close {tech.latest_close}, EMA20 {tech.latest_ema20}, vs EMA20 {pct}"


def _rating_technical_summary(rating) -> str:
    if rating is None:
        return "technical n/a"
    pct_raw = rating["price_vs_ema20_pct"] if "price_vs_ema20_pct" in rating.keys() else None
    pct = f"{float(pct_raw) * 100:+.1f}%" if pct_raw is not None else "n/a"
    return (
        f"technical {rating['technical_state']}, close {rating['latest_close']}, "
        f"EMA20 {rating['ema20']}, vs EMA20 {pct}"
    )


def _fmt_pnl(market_value: float, cost_basis: float | None) -> str:
    if cost_basis is None:
        return "n/a cost basis"
    pnl = market_value - cost_basis
    pct = pnl / cost_basis if cost_basis else 0.0
    return f"{pnl:+,.0f} ({pct * 100:+.1f}%)"


def _row_json_line(row, keys: list[str]) -> str:
    out = {}
    for k in keys:
        value = row[k] if k in row.keys() else None
        if k in {"drivers", "risk_components"} and isinstance(value, str):
            try:
                value = json.loads(value or "[]" if k == "drivers" else value or "{}")
            except json.JSONDecodeError:
                pass
        out[k] = value
    return json.dumps(out, indent=2, default=str)[:5_000]


def _one_line(text: str, n: int) -> str:
    s = " ".join(text.split())
    return s[: n - 1] + "…" if len(s) > n else s


def _block(text: str, n: int) -> str:
    return text[: n - 1] + "…" if len(text) > n else text


def _live_web_context(message: str, holdings) -> dict[str, str | None]:
    searched_at = datetime.now(UTC).isoformat()
    if not holdings:
        return {"text": "", "status": "skipped_no_detected_ticker", "searched_at": None}
    if not _wants_live_web(message):
        return {"text": "", "status": "skipped_question_not_time_sensitive", "searched_at": None}

    lines = [
        "CODEX NATIVE WEB SEARCH REQUEST",
        f"Requested at: {searched_at}",
        "Use Codex GPT-5.5 native web search for current leads; verify against primary sources before treating fresh snippets as decisive.",
    ]
    for h in list(holdings)[:3]:
        entity = h.company_name or h.ticker
        query = f"{h.ticker} {entity} next catalyst FDA trial data stock latest news"
        lines.append("")
        lines.append(f"Search request for {h.ticker} {entity}:")
        lines.append(f"- Query: {query}")
    return {
        "text": "\n".join(lines),
        "status": "codex_native_web_search_requested",
        "searched_at": searched_at,
    }


def _wants_live_web(message: str) -> bool:
    lowered = message.lower()
    return any(term in lowered for term in LIVE_WEB_TERMS)
