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

# Stage classification drives Phase 3 stage_interaction (clinical × #7 capital
# and commercial × #4 revenue both get a 1.3× multiplier).
Stage = Literal["clinical_stage", "commercial_stage", "hybrid"]
ConvictionTier = Literal[1, 2, 3, 4, 5]
CatalystType = Literal["clinical", "regulatory", "commercial", "corporate", "other"]
Confidence = Literal["high", "medium", "low"]


# One row per ticker from an IBKR Flex pull. Output of the normalization
# spec defined in PLAN §1. pct_nav is precomputed against the pull's NAV
# so downstream phases can read it without re-deriving.
class Position(BaseModel):
    """One row per ticker from an IBKR Flex pull. pct_nav = market_value / NAV."""

    ticker: str
    qty: float
    market_value: float
    pct_nav: float
    cost_basis: float | None = None
    pulled_at: datetime
    nav: float

    # Uppercase tickers so dict-keyed lookups across phases hit reliably.
    @field_validator("ticker")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()


# One expected upcoming event on a ticker (clinical readout, PDUFA, etc.).
# Feeds Phase 3's catalyst_proximity_boost via Holding.nearest_catalyst_days.
class Catalyst(BaseModel):
    date: date
    type: CatalystType
    description: str
    confidence: Confidence = "medium"
    resolved: bool = False
    # When marking resolved or rolled-forward, write why — see Phase 1 sidecar
    # maintenance protocol. Stale catalyst lists corrupt the proximity boost.
    resolution_note: str | None = None


# Per-ticker manual metadata stored as YAML at data/portfolio/sidecar/{TICKER}.yaml.
# Entity identifiers (company_name, aliases, brands, products) feed Phase 2's
# query construction; conviction_tier + stage + thesis feed Phase 3 scoring.
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
    # Optional exact issuer-disclosure URLs. When present, the morning smart
    # recompute checks these before relying on search-engine discovery.
    ir_url: str | None = None
    press_releases_url: str | None = None
    press_release_rss_url: str | None = None
    catalysts: list[Catalyst] = Field(default_factory=list)

    # Match Position's ticker normalization so joins on ticker work.
    @field_validator("ticker")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()


# Joined view of Position ⨝ Sidecar plus derived catalyst-proximity fields.
# Canonical input every downstream phase reads — Phase 2 builds queries
# from it, Phase 3 scores against it, Phase 4 frames the red team.
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
    ir_url: str | None = None
    press_releases_url: str | None = None
    press_release_rss_url: str | None = None
    catalysts: list[Catalyst]
    # Derived — feeds Phase 3 catalyst_proximity_boost
    nearest_catalyst_days: int | None = None
    has_overdue_catalyst: bool = False
