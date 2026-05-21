"""Conviction tier review (PLAN.MD §7 — "Quarterly: re-validate conviction
tiers and catalyst dates against current reality").

Reads the sidecar YAML files directly (not the DB) since the sidecars are
the authoritative state. File mtime is the staleness signal — if you
haven't touched a sidecar in >90 days, you probably haven't re-validated
its conviction tier either.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

from ..portfolio.joined import latest_joined
from ..portfolio.sidecar import SIDECAR_DIR

# Default age beyond which a sidecar is considered stale and surfaces in
# the tuning report for re-validation.
STALE_DAYS_THRESHOLD = 90


# Per-tier (1..5): count of holdings + total %NAV + ticker list. Helps
# spot tier inflation (everything ends up tier 4-5) or tier compression.
def tier_distribution() -> list[dict]:
    """For each tier (1–5): n_holdings + total %NAV."""
    holdings, _missing, _ = latest_joined()
    by_tier: dict[int, dict] = {
        t: {"tier": t, "n_holdings": 0, "total_pct_nav": 0.0, "tickers": []}
        for t in range(1, 6)
    }
    for h in holdings:
        t = int(h.conviction_tier)
        by_tier.setdefault(t, {"tier": t, "n_holdings": 0,
                                "total_pct_nav": 0.0, "tickers": []})
        by_tier[t]["n_holdings"] += 1
        by_tier[t]["total_pct_nav"] += h.pct_nav
        by_tier[t]["tickers"].append(h.ticker)
    return [by_tier[t] for t in sorted(by_tier)]


# Identify sidecars whose files haven't been modified in `threshold_days`
# or longer. mtime is the staleness signal — old mtime → tier probably
# not re-validated against current reality.
def stale_sidecars(*, threshold_days: int = STALE_DAYS_THRESHOLD) -> list[dict]:
    """Sidecar files not modified in > threshold_days. Excludes `_`-prefixed."""
    if not SIDECAR_DIR.exists():
        return []
    now = datetime.now(timezone.utc)
    out: list[dict] = []
    for p in sorted(SIDECAR_DIR.glob("*.yaml")):
        if p.stem.startswith("_"):
            continue
        mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
        age_days = (now - mtime).days
        if age_days >= threshold_days:
            out.append({
                "ticker": p.stem,
                "last_modified": mtime.isoformat(),
                "age_days": age_days,
                "path": str(p),
            })
    return out


# Render the conviction-review section: tier distribution table +
# stale-sidecar list. Used by the tuning report.
def render_tier_markdown(
    distribution: Sequence[dict],
    stale: Sequence[dict],
    *,
    threshold_days: int = STALE_DAYS_THRESHOLD,
) -> str:
    parts: list[str] = []
    parts.append("**Tier distribution:**")
    parts.append("")
    parts.append("| Tier | Holdings | Total %NAV | Tickers |")
    parts.append("|-----:|---------:|-----------:|---------|")
    for row in distribution:
        tickers = ", ".join(row["tickers"]) if row["tickers"] else "—"
        parts.append(
            f"| T{row['tier']} | {row['n_holdings']} | "
            f"{row['total_pct_nav'] * 100:.2f}% | {tickers} |"
        )
    parts.append("")
    if stale:
        parts.append(f"**Stale sidecars** (not modified in ≥{threshold_days} days — "
                     f"re-validate tier + catalyst dates):")
        parts.append("")
        for s in stale:
            parts.append(f"- `{s['ticker']}.yaml` — {s['age_days']} days old "
                         f"(last modified {s['last_modified'][:10]})")
    else:
        parts.append(f"*No sidecars are stale (all modified in last {threshold_days} days).*")
    return "\n".join(parts)
