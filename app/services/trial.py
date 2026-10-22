"""Free trial subscriptions.

A trial is an order with a zero price that is delivered immediately. Every user
can take exactly one; referred users can be given a longer trial through the
shop settings.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Order, User, VPNAccount


@dataclass
class TrialSettings:
    enabled: bool = True
    days: int = 1
    gb: int = 1


async def get_trial_settings(session: AsyncSession) -> TrialSettings:
    from app.services.bot_settings import get_bot_settings

    row = await get_bot_settings(session)
    return TrialSettings(
        enabled=bool(row.trial_enabled),
        days=max(int(row.trial_days or 0), 0),
        gb=max(int(row.trial_gb or 0), 0),
    )


async def set_trial_settings(
    session: AsyncSession,
    *,
    enabled: bool | None = None,
    days: int | None = None,
    gb: int | None = None,
) -> TrialSettings:
    from app.services.bot_settings import set_bot_settings

    await set_bot_settings(
        session, trial_enabled=enabled, trial_days=days, trial_gb=gb
    )
    return await get_trial_settings(session)


async def has_used_trial(session: AsyncSession, user_id: int) -> bool:
    result = await session.execute(
        select(func.count(Order.id)).where(
            Order.user_id == user_id, Order.is_trial.is_(True)
        )
    )
    return int(result.scalar_one() or 0) > 0


async def has_active_subscription(session: AsyncSession, user_id: int) -> bool:
    """True when the user already has a live account."""
    result = await session.execute(
        select(func.count(VPNAccount.id)).where(
            VPNAccount.user_id == user_id,
            VPNAccount.is_active.is_(True),
            VPNAccount.expires_at > datetime.utcnow(),
        )
    )
    return int(result.scalar_one() or 0) > 0


async def trial_available(
    session: AsyncSession, user_id: int
) -> tuple[bool, str]:
    """(can_take_trial, reason_when_not)."""
    settings = await get_trial_settings(session)
    if not settings.enabled:
        return False, "پلن تست رایگان غیرفعال است."
    if settings.days <= 0 and settings.gb <= 0:
        return False, "پلن تست رایگان تنظیم نشده است."
    if await has_used_trial(session, user_id):
        return False, "شما قبلاً از پلن تست رایگان استفاده کرده‌اید."
    if await has_active_subscription(session, user_id):
        return False, "شما هم‌اکنون اشتراک فعال دارید."
    return True, ""


async def create_trial_order(
    session: AsyncSession,
    user: User,
    settings: TrialSettings,
) -> Optional[Order]:
    """Create the zero-price trial order (delivery happens in the caller)."""
    order = Order(
        user_id=user.id,
        days=max(settings.days, 0),
        traffic_gb=max(settings.gb, 0),
        price=0,
        original_price=0,
        is_trial=True,
        device_limit=1,
        status="paid",
    )
    session.add(order)
    await session.flush()
    return order


async def claim_trial(session: AsyncSession, user: User) -> bool:
    """
    Atomically claim the one free trial of this user.

    Returns False when the trial was already taken, so two parallel requests
    can never create two free subscriptions.
    """
    claimed = await session.execute(
        update(User)
        .where(User.id == user.id, User.trial_used_at.is_(None))
        .values(trial_used_at=datetime.utcnow())
        .execution_options(synchronize_session=False)
    )
    return claimed.rowcount == 1
