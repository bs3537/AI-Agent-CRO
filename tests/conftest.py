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
