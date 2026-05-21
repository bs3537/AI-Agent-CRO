from pathlib import Path

from .config import settings

# Root data directory resolved to an absolute path so every phase agrees
# on where files live regardless of the caller's cwd.
DATA_ROOT = Path(settings.data_root).resolve()

# Per-phase data subdirectories. Each phase reads/writes only within
# its own subdir so the on-disk layout matches the module boundaries.
PORTFOLIO_DIR = DATA_ROOT / "portfolio"           # Phase 1: Flex XML + sidecars
NEWS_CACHE_DIR = DATA_ROOT / "news_cache"         # Phase 2: Exa fixtures
SCORES_DIR = DATA_ROOT / "scores"                 # Phase 3: rubric + calibration
DIGESTS_DIR = DATA_ROOT / "digests"               # Phase 5: digest + alert archive
WARNING_SIGNS_DIR = DATA_ROOT / "warning_signs"   # Phase 4: catalog.yaml
LOGS_DIR = DATA_ROOT / "logs"                     # Phase 6: cron/systemd log mirror
FACTOR_BUCKETS_DIR = DATA_ROOT / "factor_buckets" # Phase 2: 12-bucket taxonomy
TUNING_DIR = DATA_ROOT / "tuning"                 # Phase 7: dated tuning reports

# Single SQLite database shared by every phase. WAL mode + foreign keys
# are enabled in db.py's connection helper.
DB_PATH = DATA_ROOT / "sma.db"

# Full list of directories that ensure_dirs() must create at bootstrap.
ALL_DIRS = [
    PORTFOLIO_DIR,
    NEWS_CACHE_DIR,
    SCORES_DIR,
    DIGESTS_DIR,
    WARNING_SIGNS_DIR,
    LOGS_DIR,
    FACTOR_BUCKETS_DIR,
    TUNING_DIR,
]


# Create every data subdirectory if missing. Called by Phase 0 bootstrap
# and by each phase's CLI main() before doing work.
def ensure_dirs() -> None:
    for d in ALL_DIRS:
        d.mkdir(parents=True, exist_ok=True)
