import re
from typing import Optional

from app.database.models import User


def _sanitize_email_part(value: str, max_len: int = 48) -> str:
    cleaned = re.sub(r"[^\w.\-]", "_", value.strip(), flags=re.UNICODE)
    return cleaned[:max_len] or "user"


def panel_client_email(user: User) -> str:
    """
    Stable panel client email.
    Primary: Telegram numeric id. Fallback: username, then first_name.
    """
    if user.id:
        return str(user.id)
    if user.username:
        return _sanitize_email_part(user.username)
    if user.first_name:
        return _sanitize_email_part(user.first_name)
    return "unknown"


def build_client_comment(user: User) -> str:
    """Panel client comment — Telegram profile only (no order/config details)."""
    parts = [f"id={user.id}"]
    if user.username:
        parts.append(f"username=@{user.username}")
    name_parts = [p for p in (user.first_name, user.last_name) if p]
    if name_parts:
        parts.append(f"name={' '.join(name_parts)}")
    return " | ".join(parts)[:500]
