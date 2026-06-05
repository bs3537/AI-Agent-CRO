"""Load and persist per-ticker sidecar YAML files.

Layout: data/portfolio/sidecar/{TICKER}.yaml. Files starting with '_' are
treated as examples and skipped (so _example.yaml ships safely in-repo).
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import yaml

from ..db import connection
from ..paths import PORTFOLIO_DIR
from .schema import Sidecar

# Directory holding one YAML file per ticker. Files starting with `_` are
# skipped by the loader so example templates can live alongside real data.
SIDECAR_DIR = PORTFOLIO_DIR / "sidecar"

SIDECAR_SCHEMA = """
CREATE TABLE IF NOT EXISTS portfolio_sidecars (
    ticker       TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    source       TEXT NOT NULL DEFAULT 'db',
    updated_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_portfolio_sidecars_updated_at
    ON portfolio_sidecars(updated_at);
"""


# Return the absolute path to a ticker's sidecar YAML. Normalizes the
# ticker to uppercase so callers can pass either form.
def sidecar_path(ticker: str) -> Path:
    return SIDECAR_DIR / f"{ticker.strip().upper()}.yaml"


# Create the sidecar directory if missing. Called before any write.
def _ensure_dir() -> None:
    SIDECAR_DIR.mkdir(parents=True, exist_ok=True)


def init_sidecar_schema(*, seed_from_yaml: bool = True) -> None:
    with connection() as conn:
        conn.executescript(SIDECAR_SCHEMA)
        if not seed_from_yaml:
            return
        now = datetime.now(UTC).isoformat()
        for sc in _load_yaml_sidecars().values():
            conn.execute(
                """INSERT OR IGNORE INTO portfolio_sidecars
                   (ticker, payload_json, source, updated_at)
                   VALUES (?, ?, ?, ?)""",
                (
                    sc.ticker,
                    json.dumps(sc.model_dump(mode="json", exclude_none=True)),
                    "yaml_seed",
                    now,
                ),
            )


def _load_yaml_sidecar(ticker: str) -> Sidecar | None:
    p = sidecar_path(ticker)
    if not p.exists():
        return None
    with p.open() as f:
        return Sidecar.model_validate(yaml.safe_load(f))


def _load_yaml_sidecars() -> dict[str, Sidecar]:
    _ensure_dir()
    out: dict[str, Sidecar] = {}
    for p in sorted(SIDECAR_DIR.glob("*.yaml")):
        if p.stem.startswith("_"):
            continue
        with p.open() as f:
            sc = Sidecar.model_validate(yaml.safe_load(f))
        out[sc.ticker] = sc
    return out


# Load one ticker's sidecar; returns None when the file is absent. The
# Phase 1 join helper uses None to flag positions missing metadata.
def load_sidecar(ticker: str) -> Sidecar | None:
    init_sidecar_schema()
    want = ticker.strip().upper()
    with connection() as conn:
        row = conn.execute(
            "SELECT payload_json FROM portfolio_sidecars WHERE ticker = ?",
            (want,),
        ).fetchone()
    if row is not None:
        return Sidecar.model_validate(json.loads(row["payload_json"]))
    return _load_yaml_sidecar(want)


# Load every sidecar in the directory into a {ticker: Sidecar} dict.
# Skips `_`-prefixed files so example templates can ship in the repo.
def load_all_sidecars() -> dict[str, Sidecar]:
    init_sidecar_schema()
    out: dict[str, Sidecar] = {}
    with connection() as conn:
        rows = conn.execute(
            "SELECT payload_json FROM portfolio_sidecars ORDER BY ticker"
        ).fetchall()
    for row in rows:
        sc = Sidecar.model_validate(json.loads(row["payload_json"]))
        out[sc.ticker] = sc
    return out


# Serialize a Sidecar back to its on-disk YAML file. Used for programmatic
# edits (e.g., bulk resolution_note updates after catalysts resolve).
def write_sidecar(sc: Sidecar) -> Path:
    init_sidecar_schema()
    sc.ticker = sc.ticker.strip().upper()
    now = datetime.now(UTC).isoformat()
    payload = json.dumps(sc.model_dump(mode="json", exclude_none=True))
    with connection() as conn:
        conn.execute(
            """INSERT INTO portfolio_sidecars(ticker, payload_json, source, updated_at)
               VALUES (?, ?, 'db', ?)
               ON CONFLICT(ticker) DO UPDATE SET
                 payload_json = excluded.payload_json,
                 source = 'db',
                 updated_at = excluded.updated_at""",
            (sc.ticker, payload, now),
        )
    p = sidecar_path(sc.ticker)
    if os.environ.get("SMA_WRITE_SIDECAR_YAML", "").strip() == "1":
        _ensure_dir()
        with p.open("w") as f:
            yaml.safe_dump(
                sc.model_dump(mode="json", exclude_none=True),
                f,
                sort_keys=False,
                default_flow_style=False,
            )
    return p


def ensure_sidecar(
    ticker: str,
    *,
    company_name: str | None = None,
    thesis: str | None = None,
) -> Sidecar:
    """Load or create a minimal sidecar for a held ticker."""
    sc = load_sidecar(ticker)
    if sc is not None:
        if company_name and not sc.company_name:
            sc.company_name = company_name
            write_sidecar(sc)
        return sc
    thesis = thesis or (
        "STUB - auto-scaffolded; replace with the actual long-term buying thesis."
    )
    sc = Sidecar(
        ticker=ticker,
        conviction_tier=3,
        stage="hybrid",
        thesis=thesis,
        company_name=company_name,
    )
    write_sidecar(sc)
    return sc


def set_ir_urls(
    ticker: str,
    *,
    ir_url: str | None = None,
    press_releases_url: str | None = None,
    press_release_rss_url: str | None = None,
    company_name: str | None = None,
    create: bool = False,
) -> Sidecar | None:
    """Persist discovered IR URLs without changing thesis/catalysts."""
    sc = ensure_sidecar(ticker, company_name=company_name) if create else load_sidecar(ticker)
    if sc is None:
        return None
    if company_name and not sc.company_name:
        sc.company_name = company_name
    if ir_url:
        sc.ir_url = ir_url
    if press_releases_url:
        sc.press_releases_url = press_releases_url
    if press_release_rss_url:
        sc.press_release_rss_url = press_release_rss_url
    write_sidecar(sc)
    return sc


# Edit one ticker's thesis (W4 — the dashboard's thesis box). Loads the
# existing sidecar and updates only .thesis; if none exists, creates a minimal
# sidecar with neutral defaults (tier 3, hybrid stage) the manager can refine
# later. Returns the saved Sidecar.
def set_thesis(ticker: str, thesis: str) -> Sidecar:
    sc = load_sidecar(ticker)
    if sc is None:
        sc = Sidecar(ticker=ticker, conviction_tier=3, stage="hybrid", thesis=thesis)
    else:
        sc.thesis = thesis
    write_sidecar(sc)
    return sc
