"""Cost tracking + degrade cascade (PLAN.MD §6).

Per-call token usage from the Anthropic SDK lands here. today_spent_usd()
sums the ledger; DegradeState applies the cascade thresholds.

Cascade (post-batch-model):
  60% budget → drop red team on T₂-T band (still red-team above T)
  75% budget → skip Opus narrative on the daily digest (template fallback)
  85% budget → drop bucket #10 (literature) + #11 (policy) query templates
  95% budget → drop bucket #12 (microstructure)

Tune DAILY_BUDGET_USD here once you have a sense of real-world spend per day.
Sonnet 4.6 ~ $3/M in, $15/M out; Opus 4.7 ~ $15/M in, $75/M out.
Cache-read tokens are billed at 10% of standard input rate.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .store import cost_sum_since, save_cost_row

log = logging.getLogger("sma_monitor.orchestrator.cost")

# Daily spending cap that the cascade thresholds are computed against.
# Tune once you have empirical data on per-day spend.
DAILY_BUDGET_USD = 5.00

# Per-million-token prices in USD. cache_write ≈ 1.25× input;
# cache_read ≈ 0.10× input. Unknown models conservatively assume Sonnet.
_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {
        "input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75,
    },
    "claude-opus-4-7": {
        "input": 15.00, "output": 75.00, "cache_read": 1.50, "cache_write": 18.75,
    },
    "heuristic-v1": {  # heuristic has no token cost
        "input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0,
    },
    "codex-cli": {  # ChatGPT-subscription login — no per-token USD billing
        "input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0,
    },
}


# Compute the dollar cost of one Claude call from its token breakdown.
# Falls back to Sonnet rates for unknown models (conservative — Sonnet is
# cheaper than Opus so this won't under-bill against a hidden Opus call).
def estimate_cost_usd(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    prices = _PRICING.get(model)
    if prices is None:
        # Unknown model — assume Sonnet rates (conservative).
        prices = _PRICING["claude-sonnet-4-6"]
    return round(
        (input_tokens * prices["input"]
         + output_tokens * prices["output"]
         + cache_read_tokens * prices["cache_read"]
         + cache_write_tokens * prices["cache_write"]) / 1_000_000,
        6,
    )


# Translate an Anthropic SDK Usage object into a cost_ledger row. Returns
# the USD cost recorded. Heuristic calls write nothing and return 0.
def record_usage(
    *,
    kind: str,
    model: str,
    usage: Any | None,
    related_event_id: str | None = None,
) -> float:
    """Translate an Anthropic Usage object into a cost_ledger row.

    Returns the recorded cost in USD. usage=None (heuristic mode) writes
    nothing and returns 0.
    """
    if usage is None or model.startswith("heuristic"):
        return 0.0
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    cache_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    cost_usd = estimate_cost_usd(
        model,
        input_tokens=input_tokens, output_tokens=output_tokens,
        cache_read_tokens=cache_read, cache_write_tokens=cache_write,
    )
    save_cost_row(
        kind=kind, model=model,
        input_tokens=input_tokens, output_tokens=output_tokens,
        cache_read_tokens=cache_read, cache_write_tokens=cache_write,
        cost_usd=cost_usd, related_event_id=related_event_id,
        incurred_at=datetime.now(timezone.utc),
    )
    return cost_usd


# Record a zero-cost LLM call to the ledger. Used for subscription-login
# backends (Codex) that have no per-token USD price — we still want call
# counts visible in `status` and available for a future call-count budget.
def record_llm_call(
    *,
    kind: str,
    model: str,
    related_event_id: str | None = None,
) -> None:
    try:
        save_cost_row(
            kind=kind, model=model,
            input_tokens=0, output_tokens=0,
            cache_read_tokens=0, cache_write_tokens=0,
            cost_usd=0.0, related_event_id=related_event_id,
            incurred_at=datetime.now(timezone.utc),
        )
    except Exception:
        pass  # cost recording must never break the calling path


# Sum today's spend from the cost ledger (UTC midnight forward). Used by
# the cascade and the digest's System status section.
def today_spent_usd(now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    summary = cost_sum_since(midnight)
    return float(summary["total_cost"])


# Snapshot of today's cost vs budget plus the four cascade boolean flags.
# Frozen so any code path holding a snapshot won't see it mutate mid-cycle.
@dataclass(frozen=True)
class DegradeState:
    """Operational decisions based on today's cumulative cost vs. budget."""

    spent_usd: float
    budget_usd: float
    # Cascade flags
    skip_red_team_t2_t_band: bool   # step 1 (60%): only red-team above T
    skip_opus_narrative: bool       # step 2 (75%): skip Opus, use template narrative
    drop_buckets_10_11: bool        # step 3 (85%): skip literature + policy queries
    drop_bucket_12: bool            # step 4 (95%): skip microstructure queries

    # Fraction of daily budget spent. 0 when budget_usd is unset.
    @property
    def fraction_spent(self) -> float:
        if self.budget_usd <= 0:
            return 0.0
        return self.spent_usd / self.budget_usd

    # Set of bucket ids the news poll should skip given the current cascade
    # state. Empty until step 3 kicks in at 85% spend.
    @property
    def skipped_bucket_ids(self) -> set[int]:
        out: set[int] = set()
        if self.drop_buckets_10_11:
            out.update({10, 11})
        if self.drop_bucket_12:
            out.add(12)
        return out


# Compute today's DegradeState. Pure function over today's cost ledger;
# called by every orchestration step that needs to decide whether to
# degrade gracefully.
def current_degrade_state(
    *,
    budget_usd: float = DAILY_BUDGET_USD,
    now: datetime | None = None,
) -> DegradeState:
    spent = today_spent_usd(now=now)
    return DegradeState(
        spent_usd=round(spent, 4),
        budget_usd=budget_usd,
        skip_red_team_t2_t_band=spent >= budget_usd * 0.60,
        skip_opus_narrative=spent >= budget_usd * 0.75,
        drop_buckets_10_11=spent >= budget_usd * 0.85,
        drop_bucket_12=spent >= budget_usd * 0.95,
    )
