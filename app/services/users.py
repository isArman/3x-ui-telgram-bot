"""User persistence helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from aiogram.types import User as TelegramUser
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Order, Payment, User, VPNAccount
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


async def set_user_blocked(
    session: AsyncSession, user_id: int, blocked: bool
) -> User | None:
    """Block or unblock a user. Returns the user, or None when unknown."""
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        return None
    user.is_blocked = bool(blocked)
    await session.commit()
    await session.refresh(user)
    return user


async def find_user(
    session: AsyncSession, query: str
) -> User | None:
    """
    Look a user up by numeric id, @username or first name.

    The bot is also usable by admins who only know the customer's username.
    """
    query = (query or "").strip().lstrip("@")
    if not query:
        return None

    if query.lstrip("-").isdigit():
        result = await session.execute(
            select(User).where(User.id == int(query))
        )
        user = result.scalar_one_or_none()
        if user:
            return user

    result = await session.execute(
        select(User)
        .where(User.username.ilike(f"%{query}%"))
        .order_by(User.id.desc())
        .limit(1)
    )
    user = result.scalar_one_or_none()
    if user:
        return user

    result = await session.execute(
        select(User)
        .where(User.first_name.ilike(f"%{query}%"))
        .order_by(User.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


@dataclass
class UserSummary:
    """Everything the admin editor shows about one customer."""

    user: User
    balance: int = 0
    total_orders: int = 0
    completed_orders: int = 0
    pending_payments: int = 0
    total_spent: int = 0
    active_accounts: int = 0
    referred_by: Optional[int] = None
    referrals: int = 0

    @property
    def display_name(self) -> str:
        name = " ".join(
            part for part in (self.user.first_name, self.user.last_name) if part
        ).strip()
        if self.user.username:
            return f"{name or self.user.username} (@{self.user.username})".strip()
        return name or str(self.user.id)


async def get_user_summary(
    session: AsyncSession, user_id: int
) -> UserSummary | None:
    """Collect the numbers the admin user editor displays."""
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        return None

    order_rows = (
        await session.execute(
            select(
                func.count(Order.id),
                func.coalesce(func.sum(Order.price), 0),
            ).where(Order.user_id == user_id)
        )
    ).one()
    completed = (
        await session.execute(
            select(func.count(Order.id)).where(
                Order.user_id == user_id, Order.status == "completed"
            )
        )
    ).scalar_one()
    pending = (
        await session.execute(
            select(func.count(Payment.id)).where(
                Payment.user_id == user_id, Payment.status == "pending"
            )
        )
    ).scalar_one()
    accounts = (
        await session.execute(
            select(func.count(VPNAccount.id)).where(
                VPNAccount.user_id == user_id, VPNAccount.is_active.is_(True)
            )
        )
    ).scalar_one()
    referrals = (
        await session.execute(
            select(func.count(User.id)).where(User.referred_by_user_id == user_id)
        )
    ).scalar_one()

    return UserSummary(
        user=user,
        balance=int(user.balance or 0),
        total_orders=int(order_rows[0] or 0),
        completed_orders=int(completed or 0),
        pending_payments=int(pending or 0),
        total_spent=int(order_rows[1] or 0),
        active_accounts=int(accounts or 0),
        referred_by=user.referred_by_user_id,
        referrals=int(referrals or 0),
    )
