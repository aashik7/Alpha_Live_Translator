"""Logging helpers with secret redaction (V2)."""

import logging
import re
from typing import Optional

_CONFIGURED = False

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
