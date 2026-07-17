"""Fail-closed evidence value helpers — null/missing/unknown never count as success."""

from __future__ import annotations

from typing import Any, Optional


class StrictEvidenceError(ValueError):
    """Raised when a required evidence value fails a strict check."""


def _missing(value: Any) -> bool:
    return value is None


def require_true(value: Any, field_name: str) -> bool:
    if value is True:
        return True
    raise StrictEvidenceError(
        f"{field_name}: expected exactly boolean True, got {value!r}"
    )


def require_false(value: Any, field_name: str) -> bool:
    if value is False:
        return True
    raise StrictEvidenceError(
        f"{field_name}: expected exactly boolean False, got {value!r}"
    )


def require_zero(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        raise StrictEvidenceError(
            f"{field_name}: expected numeric zero, got bool {value!r}"
        )
    if isinstance(value, (int, float)) and value == 0:
        return True
    raise StrictEvidenceError(
        f"{field_name}: expected numeric zero, got {value!r}"
    )


def require_non_empty(value: Any, field_name: str) -> bool:
    if _missing(value):
        raise StrictEvidenceError(f"{field_name}: missing/null")
    if isinstance(value, str) and not value.strip():
        raise StrictEvidenceError(f"{field_name}: empty string")
    if isinstance(value, (list, dict, tuple, set)) and len(value) == 0:
        raise StrictEvidenceError(f"{field_name}: empty collection")
    if value == "":
        raise StrictEvidenceError(f"{field_name}: empty")
    return True


def require_numeric(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or _missing(value):
        raise StrictEvidenceError(
            f"{field_name}: expected numeric, got {value!r}"
        )
    if isinstance(value, (int, float)):
        return float(value)
    raise StrictEvidenceError(f"{field_name}: expected numeric, got {value!r}")


def is_exactly_true(value: Any) -> bool:
    return value is True


def is_exactly_false(value: Any) -> bool:
    return value is False


def is_numeric_zero(value: Any) -> bool:
    return (not isinstance(value, bool)) and isinstance(value, (int, float)) and value == 0


def try_require_true(value: Any) -> tuple[bool, Optional[str]]:
    try:
        require_true(value, "value")
        return True, None
    except StrictEvidenceError as exc:
        return False, str(exc)
