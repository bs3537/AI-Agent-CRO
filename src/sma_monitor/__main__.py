"""Phase 0 bootstrap / smoke test.

Run: python -m sma_monitor

Verifies:
  - config loads from .env
  - data directories are created
  - SQLite events table initializes
  - event_id generation works
  - reports which later-phase secrets are still missing
"""
import logging
import sys

from .config import settings
from .db import init_db
from .decision.store import init_decision_schema
from .identity import article_event_id
from .logging_setup import setup_logging
from .paths import DATA_ROOT, DB_PATH, ensure_dirs
from .news.store import init_news_schema
from .orchestrator.store import init_orchestrator_schema
from .outputs.store import init_outputs_schema
from .portfolio.store import init_portfolio_schema
from .portfolio.uploads import init_uploads_schema
from .red_team.store import init_red_team_schema
from .scorer.store import init_scores_schema


# Bootstrap entry point invoked by `python -m sma_monitor`. Initializes
# the runtime (logging, dirs, every phase's SQLite schema), verifies
# event_id generation, and reports which per-phase secrets are missing.
def main() -> int:
    setup_logging(settings.log_level)
    log = logging.getLogger("sma_monitor.bootstrap")

    log.info("bootstrap_start", extra={"data_root": str(DATA_ROOT)})

    ensure_dirs()
    log.info("data_dirs_ready")

    init_db()
    init_portfolio_schema()
    init_uploads_schema()
    init_news_schema()
    init_scores_schema()
    init_red_team_schema()
    init_outputs_schema()
    init_orchestrator_schema()
    init_decision_schema()
    log.info("db_initialized", extra={"db_path": str(DB_PATH)})

    sample = article_event_id(
        url="https://example.com/a", title="Test", lede="First sentence."
    )
    log.info("identity_check", extra={"sample_event_id": sample})

    inventory: dict[str, object] = {}
    for phase in (1, 2, 3, 4, 5):
        missing = settings.missing_for(phase)
        inventory[f"phase_{phase}"] = "ready" if not missing else f"missing: {missing}"
    log.info("secrets_inventory", extra=inventory)

    log.info("bootstrap_ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
