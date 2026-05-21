"""Load and validate the warning-signs catalog.

The catalog is the vocabulary the red team can cite from. Bump
catalog_version in catalog.yaml after any change — the pipeline keys
idempotency off it so updates trigger a re-run.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from ..paths import DATA_ROOT

# Path to the catalog file. Owned by Phase 7 — new entries are added via
# the library-growth workflow once a missed event has been recorded.
CATALOG_FILE = DATA_ROOT / "warning_signs" / "catalog.yaml"


# One warning-sign entry. The red team can cite by id; the heuristic also
# uses `keywords` to keyword-match against article text.
class WarningSign(BaseModel):
    id: str
    name: str
    buckets: list[int]
    definition: str
    keywords: list[str] = Field(default_factory=list)
    historical_example: str = ""
    invalidator: str = ""


# The full catalog. catalog_version drives Phase 4 idempotency — bumping
# this string causes a fresh red-team pass over every above-T₂ score.
class Catalog(BaseModel):
    version: int = 1
    catalog_version: str
    warning_signs: list[WarningSign]


# Load and validate the catalog YAML. Errors loudly if missing — the red
# team can't function without a catalog to cite from.
def load_catalog(path: Path = CATALOG_FILE) -> Catalog:
    if not path.exists():
        raise FileNotFoundError(f"Warning-signs catalog not found at {path}")
    with path.open() as f:
        return Catalog.model_validate(yaml.safe_load(f))


# Index the catalog by id for O(1) lookup. Used by the Claude client to
# enrich + validate matched_warning_signs returned by the model.
def by_id(catalog: Catalog) -> dict[str, WarningSign]:
    return {ws.id: ws for ws in catalog.warning_signs}


# Group catalog entries by bucket id. One sign can belong to multiple
# buckets — it shows up in every group it lists.
def by_bucket(catalog: Catalog) -> dict[int, list[WarningSign]]:
    out: dict[int, list[WarningSign]] = {}
    for ws in catalog.warning_signs:
        for bid in ws.buckets:
            out.setdefault(bid, []).append(ws)
    return out
