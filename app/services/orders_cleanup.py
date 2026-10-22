"""Housekeeping for orders that were never paid.

An order that stays "pending" forever keeps its promo code reserved and its
one pending payment row, so the customer can neither pay it nor use the code
again. This job cancels the stale ones and gives the code back.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Order, Payment, PromoCode

# An order older than this is considered abandoned.
DEFAULT_MAX_AGE_HOURS = 24

# The order states that still wait for a payment.
WAITING_STATUSES = ("pending", "awaiting_stars")


async def cancel_stale_orders(
    session: AsyncSession, *, max_age_hours: int = DEFAULT_MAX_AGE_HOURS
) -> int:
    """
    Cancel abandoned orders and release their promo codes.

    Returns the number of cancelled orders.
    """
    cutoff = datetime.utcnow() - timedelta(hours=max(1, int(max_age_hours)))
    result = await session.execute(
        select(Order).where(
            Order.status.in_(WAITING_STATUSES),
            Order.created_at < cutoff,
        )
    )
    orders = list(result.scalars().all())
    if not orders:
        return 0

    # Give every promo reservation back.
    released: dict[str, int] = {}
    for order in orders:
        if order.promo_code:
            released[order.promo_code] = released.get(order.promo_code, 0) + 1
    for code, count in released.items():
        promo = (
            await session.execute(select(PromoCode).where(PromoCode.code == code))
        ).scalar_one_or_none()
        if promo:
            promo.used_count = max(int(promo.used_count or 0) - count, 0)

    # The pending payment row blocks a new payment for the same order.
    await session.execute(
        delete(Payment).where(
            Payment.order_id.in_([order.id for order in orders]),
            Payment.status == "pending",
        )
    )

    for order in orders:
        order.status = "cancelled"

    await session.commit()
    return len(orders)
