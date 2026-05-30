"""Session-wide test isolation.

Redirects DATA_ROOT to a throwaway copy of the repo's data/ dir BEFORE any
sma_monitor module imports (conftest loads ahead of test modules). This keeps
the live data/sma.db, sidecars, and uploads untouched while giving DB-backed
tests (e.g. the API) realistic seed state — the copy includes positions,
scores, red-team passes, the warning-signs catalog, and news fixtures.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

# Copy the data dir into a temp sandbox and point DATA_ROOT at it for the whole
# test session. Set unconditionally so a developer's shell DATA_ROOT can't leak
# real data into the tests.
_SANDBOX = Path(tempfile.mkdtemp(prefix="sma_test_data_")) / "data"
shutil.copytree(REPO / "data", _SANDBOX)
os.environ["DATA_ROOT"] = str(_SANDBOX)

# Seed a deterministic positions pull (VRTX/MRNA — the tickers with committed
# sidecars) into the sandbox so DB-backed tests (the API suite) don't depend on
# whatever live IBKR pull happens to sit in the dev's data/sma.db. Stamped far
# in the future so it always wins as the "latest" pull over any real pull copied
# in above. Imported after DATA_ROOT is set so it targets the sandbox DB.
from datetime import UTC, datetime  # noqa: E402

from sma_monitor.db import init_db  # noqa: E402
from sma_monitor.portfolio.schema import Position  # noqa: E402
from sma_monitor.portfolio.store import init_portfolio_schema, save_pull  # noqa: E402

_SEED_AT = datetime(2035, 1, 1, tzinfo=UTC)
_SEED_NAV = 1_000_000.0
_SEED_POSITIONS = [
    Position(ticker="VRTX", qty=500, market_value=232_500.0,
             pct_nav=232_500.0 / _SEED_NAV, cost_basis=150_000.0,
             pulled_at=_SEED_AT, nav=_SEED_NAV),
    Position(ticker="MRNA", qty=2_000, market_value=60_000.0,
             pct_nav=60_000.0 / _SEED_NAV, cost_basis=90_000.0,
             pulled_at=_SEED_AT, nav=_SEED_NAV),
]
init_db()
init_portfolio_schema()
save_pull(_SEED_POSITIONS, nav=_SEED_NAV, pulled_at=_SEED_AT,
          source="test_seed", raw_xml=None)
