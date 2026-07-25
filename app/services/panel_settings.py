import json
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import PanelSettings
from app.utils.encryption import decrypt_if_needed, encrypt, _looks_encrypted

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
    pwd = decrypt_if_needed(settings.panel_password or "")
    if _looks_encrypted(pwd):
        logger.warning(
            "Panel password could not be decrypted — re-run panel setup in the bot."
        )
        return ""
    return pwd


def repair_panel_password(settings: PanelSettings) -> bool:
    """Re-encrypt password once if DB has nested encryption. Returns True if repaired."""
    pwd = decrypt_if_needed(settings.panel_password or "")
    if not pwd or _looks_encrypted(pwd):
        return False
    clean = encrypt(pwd)
    if settings.panel_password == clean:
        return False
    settings.panel_password = clean
    return True


def set_panel_password(settings: PanelSettings, password: str) -> None:
    settings.panel_password = encrypt(password) if password else ""


def get_selected_inbound_ids(settings: PanelSettings) -> List[int]:
    try:
        data = json.loads(settings.selected_inbound_ids or "[]")
        return [int(x) for x in data]
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def set_selected_inbound_ids(settings: PanelSettings, inbound_ids: List[int]) -> List[int]:
    cleaned = sorted({int(x) for x in inbound_ids})
    settings.selected_inbound_ids = json.dumps(cleaned)
    return cleaned


def toggle_inbound_id(settings: PanelSettings, inbound_id: int) -> List[int]:
    current = set(get_selected_inbound_ids(settings))
    if inbound_id in current:
        current.remove(inbound_id)
    else:
        current.add(inbound_id)
    return set_selected_inbound_ids(settings, sorted(current))


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
        and bool(settings.panel_username)
        and bool(get_panel_password(settings))
        and bool(settings.subscription_base_url)
        and bool(get_selected_inbound_ids(settings))
    )


@asynccontextmanager
async def xui_client_for_panel(settings: PanelSettings) -> AsyncIterator:
    """Open an authenticated XUIClient from stored panel settings."""
    from app.xui.client import XUIClient

    async with XUIClient(
        settings.panel_url,
        settings.panel_username,
        get_panel_password(settings),
    ) as client:
        yield client
