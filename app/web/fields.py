"""Typed reading of JSON request bodies.

The Mini App sends booleans and numbers as JSON, but a hand-written request
(curl, an old cached page) can send "false" or "12" as a string. Every value
that reaches the database goes through these helpers, so a wrong type becomes
a clean error instead of a silently wrong setting.
"""

from __future__ import annotations

from typing import Any, Optional

_TRUE_VALUES = {"1", "true", "yes", "on", "بله"}
_FALSE_VALUES = {"0", "false", "no", "off", "خیر", ""}


class FieldError(ValueError):
    """A request field has an unusable value."""


def as_bool(value: Any, *, field: str = "value") -> bool:
    """Read a boolean from JSON or from a form-encoded string."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_VALUES:
            return True
        if lowered in _FALSE_VALUES:
            return False
    raise FieldError(f"{field} must be a boolean")


def as_int(
    value: Any,
    *,
    field: str = "value",
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """Read an integer from JSON or from a numeric string."""
    if isinstance(value, bool):
        raise FieldError(f"{field} must be a number")
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise FieldError(f"{field} must be a number") from exc
    if minimum is not None and number < minimum:
        raise FieldError(f"{field} must be at least {minimum}")
    if maximum is not None and number > maximum:
        raise FieldError(f"{field} must be at most {maximum}")
    return number


def as_text(value: Any, *, field: str = "value", maximum: int = 500) -> str:
    """Read a trimmed string."""
    if value is None:
        return ""
    text = str(value).strip()
    if len(text) > maximum:
        raise FieldError(f"{field} is too long")
    return text


def optional_bool(body: dict[str, Any], key: str) -> Optional[bool]:
    """Read an optional boolean field; None when the key is absent."""
    if key not in body or body[key] is None:
        return None
    return as_bool(body[key], field=key)


def optional_int(
    body: dict[str, Any],
    key: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> Optional[int]:
    """Read an optional integer field; None when the key is absent."""
    if key not in body or body[key] is None:
        return None
    return as_int(body[key], field=key, minimum=minimum, maximum=maximum)


def optional_text(
    body: dict[str, Any], key: str, *, maximum: int = 500
) -> Optional[str]:
    """Read an optional string field; None when the key is absent."""
    if key not in body or body[key] is None:
        return None
    return as_text(body[key], field=key, maximum=maximum)
