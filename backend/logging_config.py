"""
Structured JSON logging — compatible dengan ELK, Loki, Grafana.

Setiap log line adalah satu JSON object:
  {"ts":"2026-06-20T10:00:00.123Z","level":"INFO","logger":"guac_sync","msg":"..."}

Extra fields yang dipass via extra={} kwarg di-merge ke root object.
"""
import json
import logging
import sys
import os
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    # Fields dari LogRecord yang sudah tercover oleh key kita sendiri — skip agar tidak noise
    _SKIP = frozenset({
        "args", "created", "exc_info", "exc_text", "filename", "funcName",
        "levelname", "levelno", "lineno", "message", "module", "msecs",
        "msg", "name", "pathname", "process", "processName", "relativeCreated",
        "stack_info", "taskName", "thread", "threadName",
    })

    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "ts":     datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level":  record.levelname,
            "logger": record.name,
            "msg":    record.getMessage(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            entry["stack"] = self.formatStack(record.stack_info)
        # Merge extra={} fields passed by caller
        for k, v in record.__dict__.items():
            if k not in self._SKIP and not k.startswith("_"):
                entry[k] = v
        return json.dumps(entry, default=str, ensure_ascii=False)


def setup_logging(level: str | None = None) -> None:
    """Configure root logger with JSON formatter. Call once at startup."""
    log_level = getattr(logging, (level or os.getenv("LOG_LEVEL", "INFO")).upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level)

    # Suppress verbose third-party loggers
    for noisy in ("uvicorn.access", "httpx", "asyncio", "paramiko"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
