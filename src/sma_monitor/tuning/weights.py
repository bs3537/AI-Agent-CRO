"""Suggest weight adjustments from precision data (PLAN.MD §7).

Rules (intentionally conservative — Phase 7 surfaces suggestions, the human
edits multipliers.py and bumps MULTIPLIERS_VERSION):

  precision < 30% AND marked ≥ MIN_MARKED   → reduce 15%, clamp to 0.30 floor
  precision > 75% AND marked ≥ 10           → bump 10%, clamp to 1.20 ceiling
  otherwise                                  → no change

The change is on bucket_weight ONLY; T / T₂ tuning and rubric anchor tuning
are surfaced as text-only recommendations because they require human
judgment that can't be derived from a precision table alone.
"""
from __future__ import annotations

from collections.abc import Sequence

from ..scorer.multipliers import BUCKET_WEIGHTS, T, T2

FLOOR = 0.30
CEILING = 1.20
REDUCE_RATIO = 0.85
INCREASE_RATIO = 1.10
NOISE_THRESHOLD = 0.30
SIGNAL_THRESHOLD = 0.75
MIN_MARKED_FOR_INCREASE = 10
MIN_MARKED_FOR_REDUCTION = 5


def suggest_weight_adjustments(precision_rows: Sequence[dict]) -> list[dict]:
    suggestions: list[dict] = []
    for r in precision_rows:
        bid = r["bucket_id"]
        if r["insufficient_data"]:
            continue
        current = BUCKET_WEIGHTS.get(bid, 1.0)
        marked = r["useful"] + r["noise"]
        prec = r["precision"] or 0.0
        if prec < NOISE_THRESHOLD and marked >= MIN_MARKED_FOR_REDUCTION:
            new = max(FLOOR, round(current * REDUCE_RATIO, 2))
            if new < current:
                suggestions.append({
                    "bucket_id": bid,
                    "action": "reduce",
                    "current_weight": current,
                    "suggested_weight": new,
                    "reason": (
                        f"Precision {prec*100:.0f}% over {marked} marked alerts "
                        f"— mostly noise. PLAN §7 says reduce, not remove."
                    ),
                })
        elif prec > SIGNAL_THRESHOLD and marked >= MIN_MARKED_FOR_INCREASE:
            new = min(CEILING, round(current * INCREASE_RATIO, 2))
            if new > current:
                suggestions.append({
                    "bucket_id": bid,
                    "action": "increase",
                    "current_weight": current,
                    "suggested_weight": new,
                    "reason": (
                        f"Precision {prec*100:.0f}% over {marked} marked alerts "
                        f"— consistently useful; bucket likely under-weighted."
                    ),
                })
    return suggestions


def threshold_recommendations(precision_rows: Sequence[dict]) -> list[str]:
    """Text-only recommendations for T / T₂ tuning."""
    recs: list[str] = []
    if not precision_rows:
        return ["Not enough alerts in window to recommend T / T₂ adjustments."]

    # Global signal-to-noise: if overall precision is very low, T is probably too low.
    total_useful = sum(r["useful"] for r in precision_rows)
    total_noise = sum(r["noise"] for r in precision_rows)
    total_marked = total_useful + total_noise
    if total_marked >= 10:
        global_prec = total_useful / total_marked
        if global_prec < 0.40:
            recs.append(
                f"Global alert precision is {global_prec*100:.0f}% over "
                f"{total_marked} marked. T={T} may be too permissive — "
                f"consider raising to {round(T * 1.1, 1)} (10% bump)."
            )
        elif global_prec > 0.75:
            recs.append(
                f"Global alert precision is {global_prec*100:.0f}% — "
                f"T={T} is well-calibrated or possibly too strict. "
                f"Consider lowering to {round(T * 0.95, 1)} only if you "
                f"feel events in the t2_to_t band should have alerted."
            )

    if not recs:
        recs.append(
            f"Current thresholds T={T} / T₂={T2} look reasonable given the "
            f"data; no adjustment suggested."
        )
    return recs


def render_suggestions_markdown(
    suggestions: Sequence[dict],
    threshold_recs: Sequence[str],
) -> str:
    parts: list[str] = []
    if not suggestions:
        parts.append("*No bucket-weight changes suggested — either insufficient data "
                     "or current weights are tracking precision.*")
    else:
        parts.append("**Bucket weights** — edit `src/sma_monitor/scorer/multipliers.py` "
                     "and bump `MULTIPLIERS_VERSION` to trigger a re-score:")
        parts.append("")
        parts.append("| Bucket | Action   | Current | Suggested | Reason |")
        parts.append("|-------:|:---------|--------:|----------:|--------|")
        for s in suggestions:
            parts.append(
                f"| #{s['bucket_id']} | {s['action']} | "
                f"{s['current_weight']:.2f} | {s['suggested_weight']:.2f} | "
                f"{s['reason']} |"
            )

    parts.append("")
    parts.append("**Thresholds:**")
    for r in threshold_recs:
        parts.append(f"- {r}")
    return "\n".join(parts)
