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
