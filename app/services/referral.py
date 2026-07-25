"""Referral codes, binding, discount, and cashback."""

from __future__ import annotations

import secrets
import string

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Order, User
from app.services.wallet import credit_balance

DISCOUNT_PERCENT = 15
CASHBACK_PERCENT = 20
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


def format_price_block(
    list_price: int,
    *,
    payable: int | None = None,
    applied: bool = False,
) -> str:
    """Human-readable price lines for confirms / checkout (no hype)."""
    original = int(list_price)
    if applied and payable is not None and int(payable) < original:
        pay = int(payable)
        saved = original - pay
        return (
            f"💰 قابل پرداخت: {pay:,} تومان\n"
            f"🏷 قیمت پلن: {original:,} تومان\n"
            f"📉 تخفیف معرفی ({DISCOUNT_PERCENT}٪): −{saved:,} تومان"
        )
    return f"💰 قیمت: {original:,} تومان"


def format_order_price_lines(order: Order) -> str:
    """Price summary for an already-created order."""
    payable = int(order.price)
    original = int(
        order.original_price if order.original_price is not None else order.price
    )
    return format_price_block(
        original,
        payable=payable,
        applied=bool(order.referral_discount_applied) and payable < original,
    )


async def grant_referral_cashback(
    session: AsyncSession, order: Order
) -> tuple[int, int] | None:
    """
    Credit referrer 20% of original_price once for a discounted first purchase.
    Returns (referrer_id, cashback_amount) or None if not granted.
    """
    if order.referral_cashback_paid:
        return None
    if not order.referral_discount_applied:
        return None
    if order.renew_vpn_account_id:
        return None

    # Only one cashback ever per referred buyer
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
        order.referral_cashback_paid = True
        await session.flush()
        return None

    buyer_result = await session.execute(select(User).where(User.id == order.user_id))
    buyer = buyer_result.scalar_one_or_none()
    if not buyer or not buyer.referred_by_user_id:
        return None

    base = int(order.original_price if order.original_price is not None else order.price)
    cashback = base * CASHBACK_PERCENT // 100
    if cashback < 1:
        order.referral_cashback_paid = True
        await session.flush()
        return None

    referrer_id = int(buyer.referred_by_user_id)
    await credit_balance(session, referrer_id, cashback)
    order.referral_cashback_paid = True
    await session.flush()
    return referrer_id, cashback


def referral_link(bot_username: str, code: str) -> str:
    username = bot_username.lstrip("@")
    return f"https://t.me/{username}?start={code}"
