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

CATALOG_FILE = DATA_ROOT / "warning_signs" / "catalog.yaml"


class WarningSign(BaseModel):
    id: str
    name: str
    buckets: list[int]
    definition: str
    keywords: list[str] = Field(default_factory=list)
    historical_example: str = ""
    invalidator: str = ""


class Catalog(BaseModel):
    version: int = 1
    catalog_version: str
    warning_signs: list[WarningSign]


def load_catalog(path: Path = CATALOG_FILE) -> Catalog:
    if not path.exists():
        raise FileNotFoundError(f"Warning-signs catalog not found at {path}")
    with path.open() as f:
        return Catalog.model_validate(yaml.safe_load(f))


def by_id(catalog: Catalog) -> dict[str, WarningSign]:
    return {ws.id: ws for ws in catalog.warning_signs}


def by_bucket(catalog: Catalog) -> dict[int, list[WarningSign]]:
    out: dict[int, list[WarningSign]] = {}
    for ws in catalog.warning_signs:
        for bid in ws.buckets:
            out.setdefault(bid, []).append(ws)
    return out
