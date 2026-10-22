"""Referral codes, binding, discount, and cashback."""

from __future__ import annotations

import secrets
import string

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Order, User
from app.services.wallet import credit_balance

DISCOUNT_PERCENT = 15
CASHBACK_PERCENT = 20
# Share of the level-1 cashback that also goes to the referrer's referrer.
CASHBACK_LEVEL2_PERCENT = 5
_ALPHABET = string.ascii_lowercase + string.digits


def _to_base36(n: int) -> str:
    if n < 0:
        n = abs(n)
    chars = string.digits + string.ascii_lowercase
    if n == 0:
        return "0"
    out: list[str] = []
    while n:
        n, r = divmod(n, 36)
        out.append(chars[r])
    return "".join(reversed(out))


def generate_referral_code(user_id: int) -> str:
    """Build a short unique-ish code from user id + random suffix."""
    suffix = "".join(secrets.choice(_ALPHABET) for _ in range(4))
    return f"{_to_base36(int(user_id))}{suffix}"


async def ensure_referral_code(session: AsyncSession, user: User) -> str:
    if user.referral_code:
        return user.referral_code

    for _ in range(8):
        code = generate_referral_code(user.id)
        exists = await session.execute(
            select(User.id).where(User.referral_code == code)
        )
        if exists.scalar_one_or_none() is None:
            user.referral_code = code
            await session.flush()
            return code

    # Extremely unlikely fallback
    code = f"{user.id}{secrets.token_hex(3)}"
    user.referral_code = code
    await session.flush()
    return code


async def try_bind_referrer(
    session: AsyncSession,
    user: User,
    code: str | None,
    *,
    is_new_user: bool,
) -> bool:
    """
    Permanently bind referrer for a brand-new user only.
    Returns True if binding succeeded.
    """
    if not is_new_user or not code:
        return False
    if user.referred_by_user_id is not None:
        return False

    code = code.strip()
    if not code:
        return False

    result = await session.execute(
        select(User).where(User.referral_code == code)
    )
    referrer = result.scalar_one_or_none()
    if not referrer:
        return False
    if referrer.id == user.id:
        return False

    user.referred_by_user_id = referrer.id
    await session.flush()
    return True


async def has_completed_new_purchase(session: AsyncSession, user_id: int) -> bool:
    result = await session.execute(
        select(Order.id)
        .where(
            Order.user_id == user_id,
            Order.status == "completed",
            Order.renew_vpn_account_id.is_(None),
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def has_referral_discount_order(session: AsyncSession, user_id: int) -> bool:
    """True if user already used (or reserved) the one-time referral discount."""
    result = await session.execute(
        select(Order.id)
        .where(
            Order.user_id == user_id,
            Order.referral_discount_applied.is_(True),
            Order.status.in_(("pending", "paid", "completed")),
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def is_discount_eligible(session: AsyncSession, user: User) -> bool:
    if not user.referred_by_user_id:
        return False
    if await has_completed_new_purchase(session, user.id):
        return False
    if await has_referral_discount_order(session, user.id):
        return False
    return True


def apply_purchase_discount(original_price: int) -> tuple[int, int, bool]:
    """
    Returns (payable, original, applied).
    Discount = floor(original * 15%).
    """
    original = int(original_price)
    if original < 1:
        return original, original, False
    discount = original * DISCOUNT_PERCENT // 100
    payable = original - discount
    if payable < 1:
        payable = 1
    return payable, original, discount > 0


def preview_discounted_price(list_price: int, *, eligible: bool) -> tuple[int, int, bool]:
    """Returns (payable, original, applied) for UI previews."""
    if eligible:
        return apply_purchase_discount(list_price)
    price = int(list_price)
    return price, price, False


async def grant_referral_cashback(
    session: AsyncSession, order: Order
) -> tuple[int, int] | None:
    """
    Credit the referrer a share of a discounted first purchase.

    The share is a percentage of what the buyer actually paid, so a promo
    code can never make the shop pay out more than it received.

    Returns (referrer_id, amount) if any credit was made.
    """
    if order.referral_cashback_paid:
        return None
    if not order.referral_discount_applied:
        return None
    if order.renew_vpn_account_id:
        return None

    buyer_result = await session.execute(select(User).where(User.id == order.user_id))
    buyer = buyer_result.scalar_one_or_none()
    if not buyer or not buyer.referred_by_user_id:
        # Nothing to pay; still mark the order so it is never reconsidered.
        order.referral_cashback_paid = True
        await session.flush()
        return None

    base = max(int(order.price or 0), 0)
    cashback = base * CASHBACK_PERCENT // 100
    if cashback < 1:
        order.referral_cashback_paid = True
        await session.flush()
        return None

    # Atomically claim this order's cashback exactly once.
    claim = await session.execute(
        update(Order)
        .where(Order.id == order.id, Order.referral_cashback_paid.is_(False))
        .values(referral_cashback_paid=True)
    )
    if claim.rowcount != 1:
        order.referral_cashback_paid = True
        return None

    # Only one cashback ever per referred buyer (across orders).
    prior = await session.execute(
        select(Order.id)
        .where(
            Order.user_id == order.user_id,
            Order.referral_cashback_paid.is_(True),
            Order.id != order.id,
        )
        .limit(1)
    )
    if prior.scalar_one_or_none() is not None:
        await session.flush()
        return None

    referrer_id = int(buyer.referred_by_user_id)
    await credit_balance(session, referrer_id, cashback)
    await _add_cashback_earned(session, referrer_id, cashback)
    order.referral_cashback_paid = True
    await session.flush()
    return referrer_id, cashback


async def _add_cashback_earned(
    session: AsyncSession, user_id: int, amount: int
) -> None:
    """Remember how much cashback this customer has earned."""
    await session.execute(
        update(User)
        .where(User.id == user_id)
        .values(referral_cashback_earned=User.referral_cashback_earned + int(amount))
        .execution_options(synchronize_session=False)
    )


async def grant_referral_cashback_level2(
    session: AsyncSession, order: Order
) -> tuple[int, int] | None:
    """
    Credit the referrer's referrer a smaller share of the same order.

    Returns (user_id, amount) if a level-2 credit was made.
    """
    if order.referral_cashback_level2_paid:
        return None
    if not order.referral_discount_applied:
        return None
    if order.renew_vpn_account_id:
        return None

    buyer_result = await session.execute(select(User).where(User.id == order.user_id))
    buyer = buyer_result.scalar_one_or_none()
    if not buyer or not buyer.referred_by_user_id:
        order.referral_cashback_level2_paid = True
        await session.flush()
        return None

    level1_result = await session.execute(
        select(User).where(User.id == buyer.referred_by_user_id)
    )
    level1 = level1_result.scalar_one_or_none()
    if not level1 or not level1.referred_by_user_id:
        order.referral_cashback_level2_paid = True
        await session.flush()
        return None

    # Atomically claim this order's level-2 cashback exactly once.
    claim = await session.execute(
        update(Order)
        .where(
            Order.id == order.id,
            Order.referral_cashback_level2_paid.is_(False),
        )
        .values(referral_cashback_level2_paid=True)
    )
    if claim.rowcount != 1:
        order.referral_cashback_level2_paid = True
        return None

    # Only one level-2 credit ever per buyer (across orders).
    prior = await session.execute(
        select(Order.id)
        .where(
            Order.user_id == order.user_id,
            Order.referral_cashback_level2_paid.is_(True),
            Order.id != order.id,
        )
        .limit(1)
    )
    if prior.scalar_one_or_none() is not None:
        await session.flush()
        return None

    base = max(int(order.price or 0), 0)
    amount = base * CASHBACK_LEVEL2_PERCENT // 100
    level2_id = int(level1.referred_by_user_id)
    if amount < 1 or level2_id == order.user_id:
        await session.flush()
        return None

    await credit_balance(session, level2_id, amount)
    await _add_cashback_earned(session, level2_id, amount)
    await session.flush()
    return level2_id, amount


def referral_link(bot_username: str, code: str) -> str:
    username = bot_username.lstrip("@")
    return f"https://t.me/{username}?start={code}"
