"""Wallet balance credit/debit helpers."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User


class InsufficientBalanceError(Exception):
    """Raised when a debit exceeds the user's available balance."""


async def get_balance(session: AsyncSession, user_id: int) -> int:
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        return 0
    return int(user.balance or 0)


async def credit_balance(session: AsyncSession, user_id: int, amount: int) -> int:
    """Add amount to user balance. Returns new balance."""
    if amount <= 0:
        raise ValueError("credit amount must be positive")

    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise ValueError(f"user {user_id} not found")

    user.balance = int(user.balance or 0) + amount
    await session.flush()
    return user.balance


async def debit_balance(session: AsyncSession, user_id: int, amount: int) -> int:
    """Subtract amount from user balance. Returns new balance."""
    if amount <= 0:
        raise ValueError("debit amount must be positive")

    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise ValueError(f"user {user_id} not found")

    current = int(user.balance or 0)
    if current < amount:
        raise InsufficientBalanceError(
            f"balance {current} is less than debit {amount}"
        )

    user.balance = current - amount
    await session.flush()
    return user.balance
