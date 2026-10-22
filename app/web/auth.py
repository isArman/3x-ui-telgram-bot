"""Authentication for the Mini App.

Telegram signs the launch parameters with the bot token, so the Mini App never
has to ask for a password. See
https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any, Optional
from urllib.parse import parse_qsl


class WebAuthError(Exception):
    """Raised when the launch data cannot be trusted."""


def validate_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age: int = 86400,
) -> dict[str, Any]:
    """
    Verify the signature on Telegram's launch data.

    Returns the parsed fields. Raises WebAuthError when the data is missing,
    tampered with or too old.
    """
    if not init_data:
        raise WebAuthError("missing init data")
    if not bot_token:
        raise WebAuthError("bot token is not configured")

    pairs = parse_qsl(init_data, keep_blank_values=True)
    if not pairs:
        raise WebAuthError("malformed init data")

    data = dict(pairs)
    received_hash = data.pop("hash", "")
    if not received_hash:
        raise WebAuthError("missing signature")

    check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret_key = hmac.new(
        b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256
    ).digest()
    expected = hmac.new(
        secret_key, check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, received_hash):
        raise WebAuthError("bad signature")

    auth_date = _as_int(data.get("auth_date"))
    if max_age and auth_date and (time.time() - auth_date) > max_age:
        raise WebAuthError("launch data is too old")

    return data


def parse_user(data: dict[str, Any]) -> Optional[dict[str, Any]]:
    """The Telegram user embedded in the launch data."""
    raw = data.get("user")
    if not raw:
        return None
    try:
        user = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(user, dict):
        return None
    user_id = _as_int(user.get("id"))
    if not user_id:
        return None
    return {
        "id": user_id,
        "username": user.get("username") or "",
        "first_name": user.get("first_name") or "",
        "last_name": user.get("last_name") or "",
        "language_code": user.get("language_code") or "",
        "is_premium": bool(user.get("is_premium")),
    }


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
