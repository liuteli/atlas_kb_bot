from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - Python builds without zoneinfo are rare.
    ZoneInfo = None  # type: ignore[assignment]


SG_TZ = ZoneInfo("Asia/Singapore") if ZoneInfo else timezone(timedelta(hours=8), "Asia/Singapore")
LOG_CONTEXT_FIELDS = (
    "user_id",
    "chat_id",
    "username",
    "event",
    "command",
    "source_id",
    "review_id",
    "status",
    "summary",
)
SENSITIVE_KEY_FRAGMENTS = ("token", "secret", "password", "authorization", "prompt", "raw", "content", "text")
TOKEN_PATTERNS = (
    re.compile(r"bot\d{5,}:[A-Za-z0-9_-]+"),
    re.compile(r"\b\d{5,}:[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)(token|secret|password|prompt)=([^&\s]+)"),
)


def singapore_now() -> datetime:
    """Return the current timestamp in the bot's operational timezone."""
    return datetime.now(SG_TZ)


def sanitize_value(value: Any, max_len: int = 500) -> str:
    """Convert a log value to a bounded, secret-safe single-line string."""
    if value is None:
        return ""
    rendered = str(value).replace("\r", "\\r").replace("\n", "\\n")
    for pattern in TOKEN_PATTERNS:
        rendered = pattern.sub(lambda m: f"{m.group(1)}=<redacted>" if len(m.groups()) == 2 else "<redacted-token>", rendered)
    if len(rendered) > max_len:
        rendered = rendered[: max_len - 14] + "...<truncated>"
    return rendered


class SingaporeJsonFormatter(logging.Formatter):
    """Format log records as compact JSON with fixed production fields."""

    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": singapore_now().isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "user_id": "",
            "chat_id": "",
            "username": "",
            "event": "",
            "command": "",
            "source_id": "",
            "review_id": "",
            "status": "",
            "summary": "",
        }
        for field in LOG_CONTEXT_FIELDS:
            data[field] = sanitize_value(getattr(record, field, ""))
        if not data["summary"]:
            data["summary"] = sanitize_value(record.getMessage())
        if record.exc_info:
            exception_text = sanitize_value(self.formatException(record.exc_info), max_len=800)
            data["summary"] = sanitize_value(f"{data['summary']} exception={exception_text}", max_len=1000)
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


class DailySingaporeFileHandler(logging.Handler):
    """Write one JSON log file per Singapore calendar day."""

    def __init__(self, log_root: Path) -> None:
        super().__init__()
        self.log_root = log_root
        self.log_root.mkdir(parents=True, exist_ok=True)
        self._current_day: Optional[str] = None
        self._stream: Optional[Any] = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            day = singapore_now().strftime("%Y-%m-%d")
            if day != self._current_day:
                self._reopen(day)
            assert self._stream is not None
            self._stream.write(self.format(record) + "\n")
            self._stream.flush()
        except Exception:
            self.handleError(record)

    def _reopen(self, day: str) -> None:
        if self._stream:
            self._stream.close()
        self._current_day = day
        self._stream = (self.log_root / f"{day}.log").open("a", encoding="utf-8")

    def close(self) -> None:
        if self._stream:
            self._stream.close()
        self._stream = None
        super().close()


def setup_logger(name: str, log_root: Path) -> logging.Logger:
    """Create a production logger with daily SG-time JSON files and stderr output."""
    logger = logging.getLogger(name)
    if getattr(logger, "_knowledge_bot_configured", False):
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False

    formatter = SingaporeJsonFormatter()
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    logger.addHandler(stream)

    file_handler = DailySingaporeFileHandler(log_root)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    setattr(logger, "_knowledge_bot_configured", True)
    return logger


def log_event(
    logger: logging.Logger,
    *,
    event: str,
    level: str = "info",
    user_id: Any = "",
    chat_id: Any = "",
    username: Any = "",
    command: Any = "",
    source_id: Any = "",
    review_id: Any = "",
    status: str = "success",
    summary: Any = "",
) -> None:
    """Write a bounded structured event without exposing secrets or raw source text."""
    record = {
        "user_id": sanitize_value(user_id, 120),
        "chat_id": sanitize_value(chat_id, 120),
        "username": sanitize_value(username, 120),
        "event": sanitize_value(event, 120),
        "command": sanitize_value(command, 120),
        "source_id": sanitize_value(source_id, 180),
        "review_id": sanitize_value(review_id, 180),
        "status": sanitize_value(status, 80),
        "summary": sanitize_value(summary, 500),
    }
    level_no = getattr(logging, level.upper(), logging.INFO)
    logger.log(level_no, record["summary"] or record["event"], extra=record)
