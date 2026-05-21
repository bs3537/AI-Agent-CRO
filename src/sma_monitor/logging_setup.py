import json
import logging
import sys
from datetime import datetime, timezone

# LogRecord attribute names that belong to the logging framework's
# internals. Filtered out so user-supplied extra={...} fields land
# cleanly in the JSON payload without framework noise.
_RESERVED = {
    "args", "msg", "name", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "taskName",
}


# Custom logging formatter that emits one JSON object per LogRecord.
# Every phase logs through this so the orchestrator (Phase 6) and the
# tuning loop (Phase 7) can parse structured fields like ticker,
# bucket_id, composite, and cost without regex-mining text logs.
class JsonFormatter(logging.Formatter):
    # Convert a single LogRecord into a one-line JSON string with the
    # standard fields plus any custom attributes the caller attached.
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


# Configure the root logger to emit JSON to stdout at the given level.
# Called once at startup by every CLI's main() (see __main__.py in each
# phase package).
def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
