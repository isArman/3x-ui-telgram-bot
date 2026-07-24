"""User persistence helpers."""

from __future__ import annotations

from aiogram.types import User as TelegramUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.services.referral import ensure_referral_code


async def get_or_create_user(
    session: AsyncSession, tg_user: TelegramUser
) -> tuple[User, bool]:
    """
    Get or create user. Returns (user, created).
    Always ensures a referral_code exists.
    """
    result = await session.execute(select(User).where(User.id == tg_user.id))
    user = result.scalar_one_or_none()

    if not user:
        user = User(
            id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
            last_name=tg_user.last_name,
        )
        session.add(user)
        await session.flush()
        await ensure_referral_code(session, user)
        return user, True

    changed = False
    for attr, value in (
        ("username", tg_user.username),
        ("first_name", tg_user.first_name),
        ("last_name", tg_user.last_name),
    ):
        if getattr(user, attr) != value:
            setattr(user, attr, value)
            changed = True

    if not user.referral_code:
        await ensure_referral_code(session, user)
        changed = True

    if changed:
        await session.flush()

    return user, False


async def is_user_blocked(session: AsyncSession, user_id: int) -> bool:
    result = await session.execute(select(User.is_blocked).where(User.id == user_id))
    row = result.scalar_one_or_none()
    return bool(row)
