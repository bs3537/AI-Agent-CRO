"""V2 rating resolver.

The grade is the canonical portfolio-facing signal:
  A/B/C -> HOLD with rising attention
  D     -> SELL / thesis broken

The configured LLM is the final decision-maker when it returns a valid grade. The
deterministic risk score remains stored and visible as an audit/input layer;
it is only used as the fallback when no LLM grade is available.
"""
from __future__ import annotations

from datetime import UTC, datetime

from ..scorer.multipliers import T2, T
from .schema import (
    GRADE_ACTION,
    GRADE_ATTENTION,
    DecisionCandidate,
    Grade,
    PositionRating,
)

RATING_VERSION = "v2.1-2026.05"


def action_for_grade(grade: str):
    return GRADE_ACTION[_valid_grade(grade)]


def attention_for_grade(grade: str):
    return GRADE_ATTENTION[_valid_grade(grade)]


def rate_candidate(
    candidate: DecisionCandidate,
    *,
    thesis_h: str,
    inputs_h: str,
    note: str,
    drivers: list[str],
    confidence: float,
    model_used: str,
    compute_source: str = "scheduler",
    llm_grade: str | None = None,
) -> PositionRating:
    components = risk_components(candidate)
    risk_score = min(100.0, sum(float(v) for v in components.values()))
    deterministic = _apply_guardrails(_grade_from_score(risk_score), candidate)
    final = _resolve_llm_grade(deterministic, llm_grade)
    technical = candidate.technical

    return PositionRating(
        ticker=candidate.ticker,
        action=action_for_grade(final),
        grade=final,
        attention_state=attention_for_grade(final),
        risk_score=round(risk_score, 2),
        risk_components=components,
        latest_close=technical.latest_close if technical else None,
        ema20=technical.latest_ema20 if technical else None,
        price_vs_ema20_pct=technical.price_vs_ema20_pct if technical else None,
        technical_state=technical.technical_state if technical else "no_price_data",
        deterministic_grade=deterministic,
        llm_grade=_valid_grade(llm_grade) if llm_grade in ("A", "B", "C", "D") else None,
        final_grade=final,
        note=_rating_note(candidate, note, final),
        drivers=_rating_drivers(candidate, drivers),
        confidence=max(0.0, min(1.0, confidence)),
        thesis_hash=thesis_h,
        inputs_hash=inputs_h,
        model_used=model_used,
        compute_source=compute_source,
        rating_version=RATING_VERSION,
        decided_at=datetime.now(UTC),
    )


def risk_components(candidate: DecisionCandidate) -> dict[str, float]:
    technical_points = candidate.technical.risk_points if candidate.technical else 0
    data_quality = 5.0 if _is_placeholder_thesis(candidate.thesis) else 0.0
    return {
        "red_team_severity": float(_red_team_points(candidate.max_severity)),
        "thesis_clause_impact": 0.0,  # Placeholder until explicit thesis checks land.
        "top_article_composite": float(_composite_points(candidate.max_composite)),
        "catalyst_timing": float(_catalyst_points(candidate)),
        "technical_trend": float(technical_points),
        "unrealized_loss": float(_unrealized_loss_points(candidate.pnl_pct)),
        "data_quality": data_quality,
    }


def _red_team_points(severity: int) -> int:
    return {1: 0, 2: 8, 3: 18, 4: 28, 5: 35}.get(max(1, min(5, int(severity))), 0)


def _composite_points(composite: float) -> int:
    if composite <= 0:
        return 0
    if composite < T2:
        return round((composite / T2) * 5)
    if composite < T:
        return round(8 + ((composite - T2) / (T - T2)) * 6)
    return min(20, round(15 + ((composite - T) / T) * 5))


def _catalyst_points(candidate: DecisionCandidate) -> int:
    if candidate.has_overdue_catalyst:
        return 10
    days = candidate.nearest_catalyst_days
    if days is None:
        return 0
    if days <= 3:
        return 8
    if days <= 14:
        return 5
    return 0


def _unrealized_loss_points(pnl_pct: float | None) -> int:
    if pnl_pct is None or pnl_pct > -0.10:
        return 0
    loss_pct = abs(pnl_pct)
    if loss_pct >= 0.30:
        return 20
    if loss_pct >= 0.20:
        return 15
    return 10


def _grade_from_score(score: float) -> Grade:
    if score >= 65:
        return "D"
    if score >= 40:
        return "C"
    if score >= 20:
        return "B"
    return "A"


def _apply_guardrails(grade: Grade, candidate: DecisionCandidate) -> Grade:
    # Severe red-team evidence defines a minimum rating floor.
    if candidate.max_severity >= 5:
        return "D"
    elif candidate.max_severity >= 4:
        grade = _worse(grade, "C")

    # Placeholder/stub theses and below-EMA trends should prevent a "clean A".
    if _is_placeholder_thesis(candidate.thesis) and grade == "A":
        grade = "B"
    if candidate.technical and candidate.technical.technical_state in (
        "below_ema20",
        "extended_below_ema20",
    ) and grade == "A":
        grade = "B"
    if candidate.pnl_pct is not None and candidate.pnl_pct <= -0.10 and grade == "A":
        grade = "B"

    # Market/technical weakness alone cannot create a D. Require real thesis or
    # evidence pressure before producing a sell-review grade.
    components = risk_components(candidate)
    nontechnical = sum(
        components[k]
        for k in (
            "red_team_severity",
            "thesis_clause_impact",
            "top_article_composite",
            "catalyst_timing",
        )
    )
    if grade == "D" and nontechnical < 40:
        grade = "C"
    return grade


def _resolve_llm_grade(
    deterministic: Grade,
    llm_grade: str | None,
) -> Grade:
    if llm_grade not in ("A", "B", "C", "D"):
        return deterministic
    # LLM-final mode: deterministic risk, technicals, severity, and source
    # policy are advisory inputs to the prompt, not post-hoc overrides. If
    # the LLM returns a valid A/B/C/D grade, use it exactly.
    return _valid_grade(llm_grade)


def _rating_note(candidate: DecisionCandidate, note: str, grade: Grade) -> str:
    if note.strip():
        return note.strip()
    tech = candidate.technical.technical_state if candidate.technical else "no_price_data"
    return (
        f"Grade {grade}: peak red-team severity {candidate.max_severity}/5, "
        f"peak article composite {candidate.max_composite:.1f}, technical state {tech}."
    )


def _rating_drivers(candidate: DecisionCandidate, drivers: list[str]) -> list[str]:
    out = [d for d in drivers if d][:6]
    tech = candidate.technical
    if tech and tech.technical_state != "no_price_data":
        label = "above 20-EMA" if tech.technical_state == "above_ema20" else "below 20-EMA"
        if tech.technical_state == "extended_below_ema20":
            label = f"below 20-EMA {tech.consecutive_below_ema20}d"
        out.append(label)
    if candidate.pnl_pct is not None and candidate.pnl_pct <= -0.10:
        out.append(f"open P/L {candidate.pnl_pct * 100:+.1f}% warning")
    return out[:6]


def _is_placeholder_thesis(thesis: str) -> bool:
    text = thesis.strip().upper()
    return text.startswith("STUB") or text.startswith("PLACEHOLDER")


def _worse(a: Grade, b: Grade) -> Grade:
    return a if _rank(a) >= _rank(b) else b


def _rank(grade: str) -> int:
    return {"A": 0, "B": 1, "C": 2, "D": 3}[_valid_grade(grade)]


def _valid_grade(grade: str | None) -> Grade:
    if grade in ("A", "B", "C", "D"):
        return grade
    return "C"
