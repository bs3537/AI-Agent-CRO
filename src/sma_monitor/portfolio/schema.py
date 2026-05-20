"""Phase 1 schema.

Position    — one row per ticker, output of the IBKR Flex normalization spec.
Catalyst    — one expected upcoming event on a ticker.
Sidecar     — per-ticker manual metadata (conviction, stage, thesis, catalysts).
Holding     — joined view (Position ⨝ Sidecar). Canonical input for Phase 2+.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Stage = Literal["clinical_stage", "commercial_stage", "hybrid"]
ConvictionTier = Literal[1, 2, 3, 4, 5]
CatalystType = Literal["clinical", "regulatory", "commercial", "corporate", "other"]
Confidence = Literal["high", "medium", "low"]


class Position(BaseModel):
    """One row per ticker from an IBKR Flex pull. pct_nav = market_value / NAV."""

    ticker: str
    qty: float
    market_value: float
    pct_nav: float
    cost_basis: float | None = None
    pulled_at: datetime
    nav: float

    @field_validator("ticker")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()


class Catalyst(BaseModel):
    date: date
    type: CatalystType
    description: str
    confidence: Confidence = "medium"
    resolved: bool = False
    # When marking resolved or rolled-forward, write why — see Phase 1 sidecar
    # maintenance protocol. Stale catalyst lists corrupt the proximity boost.
    resolution_note: str | None = None


class Sidecar(BaseModel):
    """Per-ticker manual metadata. One YAML file per holding."""

    ticker: str
    conviction_tier: ConvictionTier
    stage: Stage
    thesis: str
    # Entity identifiers — feed Phase 2 query templates. company_name falls
    # back to ticker when None. aliases/brands/products/indications expand
    # recall (e.g. "Moderna", "Spikevax", "mRNA-1273", "COVID-19 vaccine").
    company_name: str | None = None
    aliases: list[str] = Field(default_factory=list)
    brands: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)
    indications: list[str] = Field(default_factory=list)
    catalysts: list[Catalyst] = Field(default_factory=list)

    @field_validator("ticker")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()


class Holding(BaseModel):
    """Joined view. What every downstream stage reads."""

    # From Position
    ticker: str
    qty: float
    market_value: float
    pct_nav: float
    cost_basis: float | None
    pulled_at: datetime
    nav: float
    # From Sidecar
    conviction_tier: ConvictionTier
    stage: Stage
    thesis: str
    company_name: str | None = None
    aliases: list[str] = Field(default_factory=list)
    brands: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)
    indications: list[str] = Field(default_factory=list)
    catalysts: list[Catalyst]
    # Derived — feeds Phase 3 catalyst_proximity_boost
    nearest_catalyst_days: int | None = None
    has_overdue_catalyst: bool = False
