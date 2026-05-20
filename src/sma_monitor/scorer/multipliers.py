"""Composite formula multipliers + thresholds.

Composite = raw_avg × bucket_weight × position_weight × conviction_mult
            × catalyst_boost × stage_interaction

raw_avg = mean(financial_impact, narrative_shift, time_criticality) in [0,10].
Multipliers have a neutral product of ~1.0; max ~5.9 (big position, tier 5,
near catalyst, matched stage); min ~0.06 (tiny position, watchlist, ignored
bucket). With raw_avg=10 the composite tops out around ~60.

Bump MULTIPLIERS_VERSION whenever a value here changes — the pipeline keys
score idempotency off this string, so re-scoring fires automatically.
"""
from __future__ import annotations

import math

# Phase 7 will tune. Bump this string on every change so re-scoring triggers.
MULTIPLIERS_VERSION = "v1.0-2026.05"

# --- Thresholds -------------------------------------------------------------
# Composite ≥ T  → real-time alert (Phase 5)
# Composite ≥ T₂ → red-team-pass + digest inclusion (Phase 4)
T: float = 15.0
T2: float = 8.0

# --- Bucket weights ---------------------------------------------------------
# Mean ~ 0.85. #12 deliberately low so noise is visible-and-suppressed (PLAN §3).
BUCKET_WEIGHTS: dict[int, float] = {
    1: 1.00,   # Clinical Development
    2: 1.00,   # Regulatory Interaction
    3: 0.80,   # Manufacturing / CMC
    4: 0.95,   # Commercial Performance
    5: 0.80,   # Competitive Landscape
    6: 0.70,   # IP & Litigation
    7: 0.95,   # Capital Structure
    8: 0.70,   # Governance & Insiders
    9: 0.85,   # Strategic Transactions
    10: 0.65,  # Scientific Literature
    11: 0.55,  # Policy Environment   (sector-scope, low per-position weight)
    12: 0.30,  # Market Microstructure  (deliberately deprioritized)
}

# --- Conviction tier → multiplier (PLAN §3) ---------------------------------
CONVICTION_MULT: dict[int, float] = {1: 0.70, 2: 0.85, 3: 1.00, 4: 1.20, 5: 1.50}

# --- Catalyst proximity (only buckets 1 & 2 per PLAN §3) --------------------
# Other buckets do NOT get the boost — applying to #4 near earnings creates
# false urgency the digest will struggle to suppress.
CATALYST_BUCKETS: set[int] = {1, 2}


def catalyst_boost(days_to_catalyst: int | None, bucket_id: int) -> float:
    if days_to_catalyst is None or bucket_id not in CATALYST_BUCKETS:
        return 1.0
    if days_to_catalyst <= 3:
        return 2.0
    if days_to_catalyst <= 14:
        return 1.5
    return 1.0


# --- Stage interaction (PLAN §3) --------------------------------------------
def stage_interaction(stage: str | None, bucket_id: int) -> float:
    if stage == "clinical_stage" and bucket_id == 7:
        return 1.3
    if stage == "commercial_stage" and bucket_id == 4:
        return 1.3
    if stage == "hybrid" and bucket_id in (4, 7):
        return 1.15
    return 1.0


# --- Position weight (log-scale on %NAV) ------------------------------------
# At pct_nav=0.01 → ~0.53; 0.05 → ~0.90; 0.10 → ~1.11; 0.24 → ~1.39.
# Log so a 24% position doesn't drown a 1% position's CRL (PLAN §3).
_POS_BASE = 0.30
_POS_SLOPE = 1.10
_POS_DENOM = math.log1p(25)  # normalize against a 25% reference


def position_weight(pct_nav: float) -> float:
    if pct_nav <= 0:
        return _POS_BASE
    val = _POS_BASE + math.log1p(pct_nav * 100) / _POS_DENOM * _POS_SLOPE
    return round(max(0.30, min(val, 1.50)), 3)


# --- Threshold band classification ------------------------------------------
def threshold_band(composite: float) -> str:
    if composite >= T:
        return "above_t"
    if composite >= T2:
        return "t2_to_t"
    return "below_t2"
