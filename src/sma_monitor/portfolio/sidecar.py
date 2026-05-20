"""Load and persist per-ticker sidecar YAML files.

Layout: data/portfolio/sidecar/{TICKER}.yaml. Files starting with '_' are
treated as examples and skipped (so _example.yaml ships safely in-repo).
"""
from __future__ import annotations

from pathlib import Path

import yaml

from ..paths import PORTFOLIO_DIR
from .schema import Sidecar

SIDECAR_DIR = PORTFOLIO_DIR / "sidecar"


def sidecar_path(ticker: str) -> Path:
    return SIDECAR_DIR / f"{ticker.strip().upper()}.yaml"


def _ensure_dir() -> None:
    SIDECAR_DIR.mkdir(parents=True, exist_ok=True)


def load_sidecar(ticker: str) -> Sidecar | None:
    p = sidecar_path(ticker)
    if not p.exists():
        return None
    with p.open() as f:
        return Sidecar.model_validate(yaml.safe_load(f))


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
