"""Assemble + persist the Phase 7 tuning report.

Writes data/tuning/YYYY-MM-DD.md. The report is read-only — Phase 7 surfaces
what to change; the user edits multipliers.py / catalog.yaml / sidecars
and bumps the relevant version strings.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ..paths import TUNING_DIR
from .bucket_review import (
    bucket_10_overlap,
    bucket_11_cluster_shape,
    cyber_breach_density,
    render_bucket_review_markdown,
    silent_buckets,
)
from .conviction_review import (
    render_tier_markdown,
    stale_sidecars,
    tier_distribution,
)
from .library_growth import (
    library_growth_candidates,
    render_candidates_markdown,
)
from .precision import (
    alert_precision_by_bucket,
    render_precision_markdown,
)
from .weights import (
    render_suggestions_markdown,
    suggest_weight_adjustments,
    threshold_recommendations,
)


def assemble_report(*, window_days: int = 14) -> str:
    today = datetime.now(timezone.utc).date().isoformat()
    parts: list[str] = []
    parts.append(f"# Tuning report — {today}")
    parts.append("")
    parts.append(
        "*PLAN.MD §7 tuning pass. Read top-to-bottom; each section names the "
        "exact file to edit if you want to act on a suggestion.*"
    )
    parts.append("")

    # --- 1. Alert precision ---
    parts.append("## 1. Per-bucket alert precision")
    parts.append("")
    precision_rows = alert_precision_by_bucket(days=window_days)
    parts.append(render_precision_markdown(precision_rows, days=window_days))
    parts.append("")

    # --- 2. Weight + threshold suggestions ---
    parts.append("## 2. Weight + threshold suggestions")
    parts.append("")
    suggestions = suggest_weight_adjustments(precision_rows)
    threshold_recs = threshold_recommendations(precision_rows)
    parts.append(render_suggestions_markdown(suggestions, threshold_recs))
    parts.append("")

    # --- 3. Library growth ---
    parts.append("## 3. Warning-signs library — growth candidates")
    parts.append("")
    candidates = library_growth_candidates(days=90)
    parts.append(render_candidates_markdown(candidates))
    parts.append("")

    # --- 4. Conviction tier review ---
    parts.append("## 4. Conviction tier review")
    parts.append("")
    parts.append(render_tier_markdown(tier_distribution(), stale_sidecars()))
    parts.append("")

    # --- 5. Bucket-level review (PLAN §7 questions) ---
    parts.append("## 5. Bucket-level architecture review")
    parts.append("")
    parts.append(render_bucket_review_markdown(
        silent_buckets(days=window_days),
        bucket_10_overlap(days=max(window_days, 28)),
        bucket_11_cluster_shape(days=max(window_days, 56)),
        cyber_breach_density(days=max(window_days, 60)),
        days=window_days,
    ))
    parts.append("")

    # --- Footer ---
    parts.append("---")
    parts.append(
        "*Actioning this report:* edit "
        "`src/sma_monitor/scorer/multipliers.py` (weights/thresholds) and "
        "bump `MULTIPLIERS_VERSION` to trigger a clean re-score. Edit "
        "`data/warning_signs/catalog.yaml` (new patterns) and bump "
        "`catalog_version` to trigger a red-team replay. Update sidecar "
        "YAMLs in `data/portfolio/sidecar/` for conviction tier changes."
    )
    return "\n".join(parts)


def write_report(*, window_days: int = 14) -> Path:
    TUNING_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()
    p = TUNING_DIR / f"{today}.md"
    p.write_text(assemble_report(window_days=window_days))
    return p
