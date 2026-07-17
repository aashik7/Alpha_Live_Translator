"""Logging helpers with secret redaction (V2)."""

import logging
import re
import time
from typing import Any, Optional

_CONFIGURED = False
_PERF_START: Optional[float] = None
_PERF_LAST: Optional[float] = None

_SECRET_PATTERNS = (
    re.compile(r"(Token\s+)([A-Za-z0-9_-]+)", re.IGNORECASE),
    re.compile(r"(DEEPGRAM_API_KEY\s*[=:]\s*)(\S+)", re.IGNORECASE),
    re.compile(r"(api[_-]?key\s*[=:]\s*)(\S+)", re.IGNORECASE),
)


def _redact_secrets(message: str) -> str:
    redacted = message
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(r"\1***REDACTED***", redacted)
    return redacted


def perf_mark_start() -> None:
    """Reset startup perf timer (call once at process entry)."""
    global _PERF_START, _PERF_LAST
    _PERF_START = time.perf_counter()
    _PERF_LAST = _PERF_START


def perf_checkpoint(checkpoint_name: str) -> None:
    """Emit a lightweight [PERF] startup checkpoint."""
    global _PERF_LAST
    if _PERF_START is None:
        perf_mark_start()
    now = time.perf_counter()
    elapsed_ms = round((now - _PERF_START) * 1000, 1)
    delta_ms = round((now - (_PERF_LAST or _PERF_START)) * 1000, 1)
    _PERF_LAST = now
    payload = {
        "checkpoint_name": checkpoint_name,
        "elapsed_ms_since_start": elapsed_ms,
        "delta_ms": delta_ms,
    }
    try:
        from alpha.constants import UI_PERFORMANCE_MODE

        quiet = bool(UI_PERFORMANCE_MODE)
    except Exception:
        quiet = False
    get_logger("alpha.perf").info("[PERF] startup checkpoint %s", payload)
    if not quiet:
        print("[PERF] startup checkpoint", payload)


def sanitize_log_data(data: Optional[dict], max_preview: int = 120) -> dict:
    """Truncate preview fields for performance-safe logging."""
    if not data:
        return {}
    try:
        from alpha.constants import LOG_PREVIEW_MAX_CHARS, PERFORMANCE_SAFE_LOGGING

        limit = LOG_PREVIEW_MAX_CHARS if PERFORMANCE_SAFE_LOGGING else max_preview
    except Exception:
        limit = max_preview
    safe: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, str) and (
            "preview" in key.lower() or key.endswith("_text")
        ):
            safe[key] = value[:limit]
        else:
            safe[key] = value
    return safe


def setup_logging(level: int = logging.INFO) -> None:
    """Configure root logger once for the application."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    _CONFIGURED = True


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return a module logger with basic setup applied."""
    setup_logging()
    return logging.getLogger(name or "alpha")


class _SafeLogger:
    """Wrapper that redacts secrets before logging or printing."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def info(self, message: str, *args, **kwargs) -> None:
        self._logger.info(_redact_secrets(str(message)), *args, **kwargs)

    def warning(self, message: str, *args, **kwargs) -> None:
        self._logger.warning(_redact_secrets(str(message)), *args, **kwargs)

    def error(self, message: str, *args, **kwargs) -> None:
        self._logger.error(_redact_secrets(str(message)), *args, **kwargs)

    def debug(self, message: str, *args, **kwargs) -> None:
        self._logger.debug(_redact_secrets(str(message)), *args, **kwargs)


def log_info(message: str) -> None:
    get_logger("alpha").info(_redact_secrets(message))


def log_warning(message: str) -> None:
    get_logger("alpha").warning(_redact_secrets(message))


def log_error(message: str) -> None:
    get_logger("alpha").error(_redact_secrets(message))


_THROTTLED_LOG_AT: dict[str, float] = {}


def should_throttle_log(key: str, interval_ms: int) -> bool:
    """Return True if this log key was emitted recently."""
    now = time.perf_counter()
    last = _THROTTLED_LOG_AT.get(key)
    if last is not None and (now - last) * 1000 < interval_ms:
        return True
    _THROTTLED_LOG_AT[key] = now
    return False


def log_throttled(key: str, message: str, data: dict, interval_ms: int = 1000) -> None:
    """Emit a log at most once per interval for a given key."""
    if should_throttle_log(key, interval_ms):
        return
    safe_data = sanitize_log_data(data)
    get_logger("alpha.perf").warning("%s %s", message, safe_data)
    try:
        from alpha.constants import DEBUG_DIAGNOSTICS, UI_PERFORMANCE_MODE

        if DEBUG_DIAGNOSTICS and not UI_PERFORMANCE_MODE:
            print(message, safe_data)
    except Exception:
        pass
