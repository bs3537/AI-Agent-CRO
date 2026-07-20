"""Operational wrapper checks for the healthcare mover refresh."""
from pathlib import Path

SCRIPT = Path("scripts/sma_healthcare_movers_refresh.py")


def test_healthcare_mover_wrapper_uses_app_venv_and_safe_timeout():
    source = SCRIPT.read_text()

    assert 'Path("/opt/data/sma-monitor")' in source
    assert "VENV_PYTHON" in source
    assert "refresh_healthcare_movers" in source
    assert "timeout=2 * 60 * 60" in source
    assert "SMA_CRON_SELF_TEST" in source
