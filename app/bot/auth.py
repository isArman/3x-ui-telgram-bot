"""Authorization helpers for the Mini App and the bot."""

from app.config.settings import settings


def is_admin(user_id: int) -> bool:
    """True when this Telegram id may open the admin panel."""
    return user_id in settings.ADMIN_IDS
