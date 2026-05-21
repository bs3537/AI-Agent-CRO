"""Load and persist per-ticker sidecar YAML files.

Layout: data/portfolio/sidecar/{TICKER}.yaml. Files starting with '_' are
treated as examples and skipped (so _example.yaml ships safely in-repo).
"""
from __future__ import annotations

from pathlib import Path

import yaml

from ..paths import PORTFOLIO_DIR
from .schema import Sidecar

# Directory holding one YAML file per ticker. Files starting with `_` are
# skipped by the loader so example templates can live alongside real data.
SIDECAR_DIR = PORTFOLIO_DIR / "sidecar"


# Return the absolute path to a ticker's sidecar YAML. Normalizes the
# ticker to uppercase so callers can pass either form.
def sidecar_path(ticker: str) -> Path:
    return SIDECAR_DIR / f"{ticker.strip().upper()}.yaml"


# Create the sidecar directory if missing. Called before any write.
def _ensure_dir() -> None:
    SIDECAR_DIR.mkdir(parents=True, exist_ok=True)


# Load one ticker's sidecar; returns None when the file is absent. The
# Phase 1 join helper uses None to flag positions missing metadata.
def load_sidecar(ticker: str) -> Sidecar | None:
    p = sidecar_path(ticker)
    if not p.exists():
        return None
    with p.open() as f:
        return Sidecar.model_validate(yaml.safe_load(f))


# Load every sidecar in the directory into a {ticker: Sidecar} dict.
# Skips `_`-prefixed files so example templates can ship in the repo.
def load_all_sidecars() -> dict[str, Sidecar]:
    _ensure_dir()
    out: dict[str, Sidecar] = {}
    for p in sorted(SIDECAR_DIR.glob("*.yaml")):
        if p.stem.startswith("_"):
            continue
        with p.open() as f:
            sc = Sidecar.model_validate(yaml.safe_load(f))
        out[sc.ticker] = sc
    return out


# Serialize a Sidecar back to its on-disk YAML file. Used for programmatic
# edits (e.g., bulk resolution_note updates after catalysts resolve).
def write_sidecar(sc: Sidecar) -> Path:
    _ensure_dir()
    p = sidecar_path(sc.ticker)
    with p.open("w") as f:
        yaml.safe_dump(
            sc.model_dump(mode="json"),
            f,
            sort_keys=False,
            default_flow_style=False,
        )
    return p
