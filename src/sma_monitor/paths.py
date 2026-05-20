from pathlib import Path

from .config import settings

DATA_ROOT = Path(settings.data_root).resolve()

PORTFOLIO_DIR = DATA_ROOT / "portfolio"
NEWS_CACHE_DIR = DATA_ROOT / "news_cache"
SCORES_DIR = DATA_ROOT / "scores"
DIGESTS_DIR = DATA_ROOT / "digests"
WARNING_SIGNS_DIR = DATA_ROOT / "warning_signs"
LOGS_DIR = DATA_ROOT / "logs"
FACTOR_BUCKETS_DIR = DATA_ROOT / "factor_buckets"

DB_PATH = DATA_ROOT / "sma.db"

ALL_DIRS = [
    PORTFOLIO_DIR,
    NEWS_CACHE_DIR,
    SCORES_DIR,
    DIGESTS_DIR,
    WARNING_SIGNS_DIR,
    LOGS_DIR,
    FACTOR_BUCKETS_DIR,
]


def ensure_dirs() -> None:
    for d in ALL_DIRS:
        d.mkdir(parents=True, exist_ok=True)
