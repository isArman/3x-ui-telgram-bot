"""Bot-wide settings (payment card) stored in DB."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings as env_settings
from app.database.models import BotSettings


async def get_bot_settings(session: AsyncSession) -> BotSettings:
    result = await session.execute(select(BotSettings).where(BotSettings.id == 1))
    row = result.scalar_one_or_none()
    if not row:
        row = BotSettings(
            id=1,
            card_number=env_settings.CARD_NUMBER or None,
            card_holder=env_settings.CARD_HOLDER or None,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row

    # One-time seed from env if DB fields empty
    changed = False
    if not (row.card_number or "").strip() and env_settings.CARD_NUMBER:
        row.card_number = env_settings.CARD_NUMBER
        changed = True
    if not (row.card_holder or "").strip() and env_settings.CARD_HOLDER:
        row.card_holder = env_settings.CARD_HOLDER
        changed = True
    if changed:
        await session.commit()
        await session.refresh(row)
    return row


async def get_card_details(session: AsyncSession) -> tuple[str, str]:
    row = await get_bot_settings(session)
    return (row.card_number or "").strip(), (row.card_holder or "").strip()


async def set_card_number(session: AsyncSession, value: str) -> BotSettings:
    row = await get_bot_settings(session)
    row.card_number = value.strip()
    await session.commit()
    await session.refresh(row)
    return row


async def set_card_holder(session: AsyncSession, value: str) -> BotSettings:
    row = await get_bot_settings(session)
    row.card_holder = value.strip()
    await session.commit()
    await session.refresh(row)
    return row


async def set_bot_settings(
    session: AsyncSession,
    *,
    maintenance_mode: bool | None = None,
    trial_enabled: bool | None = None,
    trial_days: int | None = None,
    trial_gb: int | None = None,
    stars_rate: int | None = None,
) -> BotSettings:
    """Update the shop-wide settings (only the given fields)."""
    row = await get_bot_settings(session)
    if maintenance_mode is not None:
        row.maintenance_mode = bool(maintenance_mode)
    if trial_enabled is not None:
        row.trial_enabled = bool(trial_enabled)
    if trial_days is not None:
        row.trial_days = max(int(trial_days), 0)
    if trial_gb is not None:
        row.trial_gb = max(int(trial_gb), 0)
    if stars_rate is not None:
        row.stars_rate = max(int(stars_rate), 1)
    await session.commit()
    await session.refresh(row)
    return row


async def is_maintenance(session: AsyncSession) -> bool:
    """True while the shop is closed for maintenance."""
    row = await get_bot_settings(session)
    return bool(row.maintenance_mode)
