import json
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import PanelSettings
from app.utils.encryption import (
    InvalidToken,
    decrypt,
    decrypt_if_needed,
    encrypt,
    _looks_encrypted,
)

PROVISIONING_MANUAL = "manual"
PROVISIONING_AUTO = "auto"

logger = logging.getLogger(__name__)


def migrate_plaintext_password(settings: PanelSettings) -> bool:
    """Encrypt legacy plaintext panel passwords. Never re-encrypt Fernet blobs."""
    raw = settings.panel_password or ""
    if not raw or _looks_encrypted(raw):
        return False
    set_panel_password(settings, raw)
    return True


async def get_panel_settings(session: AsyncSession) -> PanelSettings:
    result = await session.execute(select(PanelSettings).where(PanelSettings.id == 1))
    row = result.scalar_one_or_none()
    if not row:
        row = PanelSettings(id=1)
        session.add(row)
        await session.commit()
        await session.refresh(row)
    elif migrate_plaintext_password(row):
        await session.commit()
    elif repair_panel_password(row):
        await session.commit()
    return row


def get_panel_password(settings: PanelSettings) -> str:
    pwd = decrypt_if_needed(getattr(settings, "panel_password", "") or "")
    if _looks_encrypted(pwd):
        logger.warning(
            "Panel password could not be decrypted — re-run panel setup in the bot."
        )
        return ""
    return pwd


def repair_panel_password(settings: PanelSettings) -> bool:
    """
    Re-encrypt a password that was encrypted more than once. Returns True if repaired.

    Fernet blobs are non-deterministic, so re-encrypting a healthy value would
    never compare equal to the stored one and would rewrite the row on every
    read. Only a still-encrypted payload after one unwrap means the value was
    double-encrypted and really needs repairing.
    """
    blob = settings.panel_password or ""
    if not blob or not _looks_encrypted(blob):
        return False
    try:
        inner = decrypt(blob)
    except (InvalidToken, ValueError, TypeError):
        return False
    if not _looks_encrypted(inner):
        # Correctly encrypted once — nothing to repair.
        return False
    settings.panel_password = encrypt(inner)
    return True


def set_panel_password(settings: PanelSettings, password: str) -> None:
    settings.panel_password = encrypt(password) if password else ""


def get_panel_api_token(settings: PanelSettings) -> str:
    """The panel API token, if one was stored."""
    token = decrypt_if_needed(getattr(settings, "panel_api_token", "") or "")
    if _looks_encrypted(token):
        logger.warning("Panel API token could not be decrypted — re-run panel setup.")
        return ""
    return token


def set_panel_api_token(settings: PanelSettings, token: str) -> None:
    settings.panel_api_token = encrypt(token.strip()) if token and token.strip() else ""


def has_panel_credentials(settings: PanelSettings) -> bool:
    """True when the bot can talk to the panel (token or username/password)."""
    if not settings.panel_url:
        return False
    return bool(get_panel_api_token(settings) or get_panel_password(settings))


def panel_auth_mode(settings: PanelSettings) -> str:
    """Human readable auth mode, for the admin status screen."""
    if get_panel_api_token(settings):
        return "توکن API"
    if get_panel_password(settings):
        return "نام کاربری و رمز"
    return "تنظیم نشده"


def get_selected_inbound_ids(settings: PanelSettings) -> List[int]:
    try:
        data = json.loads(settings.selected_inbound_ids or "[]")
        return [int(x) for x in data]
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def set_selected_inbound_ids(settings: PanelSettings, inbound_ids: List[int]) -> List[int]:
    """Replace the whole inbound selection."""
    cleaned = sorted({int(x) for x in inbound_ids})
    settings.selected_inbound_ids = json.dumps(cleaned)
    return cleaned


def prune_selected_inbound_ids(
    settings: PanelSettings,
    valid_inbound_ids: List[int],
) -> List[int]:
    """Keep only inbound IDs that still exist on the panel. Returns the pruned list."""
    valid = set(int(x) for x in valid_inbound_ids)
    kept = [i for i in get_selected_inbound_ids(settings) if i in valid]
    return set_selected_inbound_ids(settings, kept)


def is_auto_provisioning_ready(settings: PanelSettings) -> bool:
    return (
        settings.provisioning_mode == PROVISIONING_AUTO
        and settings.is_verified
        and bool(settings.panel_url)
        and has_panel_credentials(settings)
        and bool(settings.subscription_base_url)
        and bool(get_selected_inbound_ids(settings))
    )


@asynccontextmanager
async def update_panel_settings(
    session: AsyncSession,
    *,
    panel_url: str | None = None,
    panel_base_path: str | None = None,
    panel_username: str | None = None,
    panel_password: str | None = None,
    api_token: str | None = None,
    subscription_base_url: str | None = None,
    provisioning_mode: str | None = None,
    is_verified: bool | None = None,
) -> PanelSettings:
    """Update the stored panel connection. Empty values are ignored."""
    settings = await get_panel_settings(session)
    if panel_url is not None:
        settings.panel_url = panel_url.strip().rstrip("/")
    if panel_base_path is not None:
        settings.panel_base_path = panel_base_path.strip()
    if panel_username is not None:
        settings.panel_username = panel_username.strip()
    if panel_password:
        set_panel_password(settings, panel_password)
    if api_token is not None:
        set_panel_api_token(settings, api_token)
    if subscription_base_url is not None:
        settings.subscription_base_url = subscription_base_url.strip()
    if provisioning_mode is not None:
        settings.provisioning_mode = provisioning_mode
    if is_verified is not None:
        settings.is_verified = bool(is_verified)
    await session.commit()
    await session.refresh(settings)
    return settings


async def xui_client_for_panel(settings: PanelSettings) -> AsyncIterator:
    """Open an authenticated XUIClient from stored panel settings.

    An API token is preferred: bearer callers skip the panel's CSRF check and
    the session login, so the bot never trips the panel's login limiter (5
    failed logins in 5 minutes locks an ip/username out for 15 minutes).
    The client falls back to the username/password session on its own if the
    token is rejected.
    """
    from app.xui.client import XUIClient

    async with XUIClient(
        settings.panel_url,
        settings.panel_username,
        get_panel_password(settings),
        api_token=get_panel_api_token(settings),
        base_path=settings.panel_base_path or "",
    ) as client:
        yield client
