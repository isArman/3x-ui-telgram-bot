"""Wallet balance credit/debit helpers."""

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.utils.debug_ndjson import agent_log


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

    result = await session.execute(
        update(User)
        .where(User.id == user_id)
        .values(balance=func.coalesce(User.balance, 0) + amount)
        .returning(User.balance)
    )
    row = result.first()
    if row is None:
        raise ValueError(f"user {user_id} not found")
    await session.flush()
    return int(row[0])


async def debit_balance(session: AsyncSession, user_id: int, amount: int) -> int:
    """Subtract amount from user balance atomically. Returns new balance."""
    if amount <= 0:
        raise ValueError("debit amount must be positive")

    result = await session.execute(
        update(User)
        .where(User.id == user_id, func.coalesce(User.balance, 0) >= amount)
        .values(balance=func.coalesce(User.balance, 0) - amount)
        .returning(User.balance)
    )
    row = result.first()
    if row is None:
        # #region agent log
        agent_log(
            "B",
            "wallet.py:debit_balance",
            "atomic debit rejected",
            {"user_id": user_id, "amount": amount},
            run_id="post-fix",
        )
        # #endregion
        exists = await session.execute(select(User.id).where(User.id == user_id))
        if exists.scalar_one_or_none() is None:
            raise ValueError(f"user {user_id} not found")
        current = await get_balance(session, user_id)
        raise InsufficientBalanceError(
            f"balance {current} is less than debit {amount}"
        )

    new_balance = int(row[0])
    await session.flush()
    # #region agent log
    agent_log(
        "B",
        "wallet.py:debit_balance",
        "atomic debit ok",
        {"user_id": user_id, "amount": amount, "new_balance": new_balance},
        run_id="post-fix",
    )
    # #endregion
    return new_balance
