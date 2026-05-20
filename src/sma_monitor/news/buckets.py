"""Load + validate the factor bucket taxonomy.

Single source of truth is data/factor_buckets/taxonomy.yaml. The 12 buckets
are loaded fresh on each call — small file, no point caching at this layer.
Phase 7 will tune search_terms there; downstream code reads through these
helpers and doesn't pin a snapshot.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from ..paths import FACTOR_BUCKETS_DIR

TAXONOMY_FILE = FACTOR_BUCKETS_DIR / "taxonomy.yaml"

BuildTier = Literal["A", "B", "C", "D"]
BucketScope = Literal["per_holding", "sector"]


class Bucket(BaseModel):
    id: int = Field(ge=1, le=12)
    name: str
    short_name: str
    build_tier: BuildTier
    scope: BucketScope
    description: str
    search_terms: list[str] = Field(default_factory=list)


class Taxonomy(BaseModel):
    version: int = 1
    buckets: list[Bucket]


def load_taxonomy(path: Path = TAXONOMY_FILE) -> Taxonomy:
    if not path.exists():
        raise FileNotFoundError(
            f"Taxonomy not found at {path}. Re-run bootstrap or restore from repo."
        )
    with path.open() as f:
        return Taxonomy.model_validate(yaml.safe_load(f))


def load_buckets(path: Path = TAXONOMY_FILE) -> dict[int, Bucket]:
    return {b.id: b for b in load_taxonomy(path).buckets}


def per_holding_buckets(buckets: dict[int, Bucket]) -> list[Bucket]:
    return sorted(
        (b for b in buckets.values() if b.scope == "per_holding"),
        key=lambda b: b.id,
    )


def sector_buckets(buckets: dict[int, Bucket]) -> list[Bucket]:
    return sorted(
        (b for b in buckets.values() if b.scope == "sector"),
        key=lambda b: b.id,
    )
