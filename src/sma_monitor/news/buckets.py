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

# Path to the single 12-bucket taxonomy file. Owned by Phase 7 for tuning.
TAXONOMY_FILE = FACTOR_BUCKETS_DIR / "taxonomy.yaml"

# A bucket's build_tier signals query-template richness on day 1 — A is
# fully fleshed out, D is sector-scope only. All buckets ingest in v1.
BuildTier = Literal["A", "B", "C", "D"]
BucketScope = Literal["per_holding", "sector"]


# One of the 12 factor buckets. Phase 2 uses search_terms to build queries
# and to keyword-tag returned articles; Phase 3 reads bucket weights.
class Bucket(BaseModel):
    id: int = Field(ge=1, le=12)
    name: str
    short_name: str
    build_tier: BuildTier
    scope: BucketScope
    description: str
    search_terms: list[str] = Field(default_factory=list)


# The full taxonomy file. Version increments on bucket-set changes — bumping
# does NOT auto-replay scoring (that's MULTIPLIERS_VERSION's job).
class Taxonomy(BaseModel):
    version: int = 1
    buckets: list[Bucket]


# Load and validate the YAML taxonomy. Errors loudly if the file is missing
# rather than returning an empty taxonomy, since Phase 2 can't function without it.
def load_taxonomy(path: Path = TAXONOMY_FILE) -> Taxonomy:
    if not path.exists():
        raise FileNotFoundError(
            f"Taxonomy not found at {path}. Re-run bootstrap or restore from repo."
        )
    with path.open() as f:
        return Taxonomy.model_validate(yaml.safe_load(f))


# Convenience: return the taxonomy as a {bucket_id: Bucket} dict for
# O(1) lookup. Most callers want this rather than the list form.
def load_buckets(path: Path = TAXONOMY_FILE) -> dict[int, Bucket]:
    return {b.id: b for b in load_taxonomy(path).buckets}


# Return buckets scoped per_holding, ordered by id. Phase 2 pipeline runs
# these once per (holding, bucket) pair.
def per_holding_buckets(buckets: dict[int, Bucket]) -> list[Bucket]:
    return sorted(
        (b for b in buckets.values() if b.scope == "per_holding"),
        key=lambda b: b.id,
    )


# Return sector-scope buckets ordered by id. Phase 2 runs these once per
# cycle with no ticker context — currently #11 policy + #12 microstructure.
def sector_buckets(buckets: dict[int, Bucket]) -> list[Bucket]:
    return sorted(
        (b for b in buckets.values() if b.scope == "sector"),
        key=lambda b: b.id,
    )
