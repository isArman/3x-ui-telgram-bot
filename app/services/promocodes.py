"""Promo codes: extra days, extra traffic, or a percentage discount.

A code is reserved when the order is created, so the usage counter and the
"one code per customer" rule hold even if the customer never pays.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Order, PromoCode

PROMO_KIND_DAYS = "days"
PROMO_KIND_GB = "gb"
PROMO_KIND_PERCENT = "percent"
PROMO_KINDS = (PROMO_KIND_DAYS, PROMO_KIND_GB, PROMO_KIND_PERCENT)

# Codes that are still redeemable (pending order or paid).
_RESERVED_STATUSES = ("pending", "paid", "completed")


@dataclass
class PromoApplication:
    """What a code contributes to an order."""

    code: str
    kind: str
    value: int
    bonus_days: int = 0
    bonus_gb: int = 0
    discount: int = 0
    new_price: int = 0

    @property
    def is_percent(self) -> bool:
        return self.kind == PROMO_KIND_PERCENT


def normalize_code(raw: str) -> str:
    return (raw or "").strip().upper()


async def get_promo(session: AsyncSession, code: str) -> Optional[PromoCode]:
    result = await session.execute(
        select(PromoCode).where(PromoCode.code == normalize_code(code))
    )
    return result.scalar_one_or_none()


async def list_promos(session: AsyncSession) -> list[PromoCode]:
    result = await session.execute(
        select(PromoCode).order_by(PromoCode.created_at.desc())
    )
    return list(result.scalars().all())


async def validate_promo(
    session: AsyncSession,
    code: str,
    *,
    user_id: int,
    price: int,
) -> tuple[Optional[PromoApplication], str]:
    """
    Check a code for a user.

    Returns (application, error). Exactly one of them is meaningful.
    """
    normalized = normalize_code(code)
    if not normalized:
        return None, "کد پرومو وارد نشده است."

    promo = await get_promo(session, normalized)
    if not promo:
        return None, "کد پرومو نامعتبر است."
    if not promo.is_active:
        return None, "این کد پرومو غیرفعال است."
    if promo.expires_at and promo.expires_at <= datetime.utcnow():
        return None, "مهلت این کد پرومو تمام شده است."

    # ``used_count`` is the single source of truth: it is incremented with a
    # conditional update when the order is created, so two parallel orders can
    # never redeem a code that only had one use left.
    if promo.max_uses and int(promo.used_count or 0) >= promo.max_uses:
        return None, "ظرفیت این کد پرومو تکمیل شده است."

    # One code per customer: a code already used on a live order cannot be reused.
    mine = await session.execute(
        select(func.count(Order.id)).where(
            Order.user_id == user_id,
            Order.promo_code == promo.code,
            Order.status.in_(_RESERVED_STATUSES),
        )
    )
    if int(mine.scalar_one() or 0) > 0:
        return None, "شما قبلاً از این کد استفاده کرده‌اید."

    if promo.kind == PROMO_KIND_PERCENT:
        if not 1 <= promo.value <= 100:
            return None, "درصد این کد پرومو نامعتبر است."
        discount = price * promo.value // 100
        new_price = max(price - discount, 0)
        application = PromoApplication(
            code=promo.code,
            kind=promo.kind,
            value=promo.value,
            discount=discount,
            new_price=new_price,
        )
    elif promo.kind == PROMO_KIND_DAYS:
        if promo.value <= 0:
            return None, "مقدار این کد پرومو نامعتبر است."
        application = PromoApplication(
            code=promo.code,
            kind=promo.kind,
            value=promo.value,
            bonus_days=promo.value,
        )
    elif promo.kind == PROMO_KIND_GB:
        if promo.value <= 0:
            return None, "مقدار این کد پرومو نامعتبر است."
        application = PromoApplication(
            code=promo.code,
            kind=promo.kind,
            value=promo.value,
            bonus_gb=promo.value,
        )
    else:
        return None, "نوع این کد پرومو پشتیبانی نمی‌شود."

    return application, ""


async def reserve_promo(
    session: AsyncSession,
    promo: PromoCode,
) -> bool:
    """
    Claim one redemption of a code.

    The counter is raised with a conditional update, so a code with one use
    left can never be redeemed twice. Returns False when the code is full.
    """
    claimed = await session.execute(
        update(PromoCode)
        .where(
            PromoCode.id == promo.id,
            PromoCode.is_active.is_(True),
            (PromoCode.max_uses == 0) | (PromoCode.used_count < PromoCode.max_uses),
        )
        .values(used_count=PromoCode.used_count + 1)
        .execution_options(synchronize_session=False)
    )
    return claimed.rowcount == 1


async def create_promo(
    session: AsyncSession,
    *,
    code: str,
    kind: str,
    value: int,
    max_uses: int = 0,
    expires_at: datetime | None = None,
    created_by: int | None = None,
) -> PromoCode:
    normalized = normalize_code(code)
    if not normalized:
        raise ValueError("کد پرومو نمی‌تواند خالی باشد.")
    if kind not in PROMO_KINDS:
        raise ValueError("نوع کد پرومو نامعتبر است.")
    if value <= 0:
        raise ValueError("مقدار کد پرومو باید بزرگ‌تر از صفر باشد.")

    existing = await get_promo(session, normalized)
    if existing:
        raise ValueError("این کد پرومو از قبل وجود دارد.")

    promo = PromoCode(
        code=normalized,
        kind=kind,
        value=int(value),
        max_uses=int(max_uses or 0),
        expires_at=expires_at,
        created_by=created_by,
    )
    session.add(promo)
    await session.flush()
    return promo


async def set_promo_active(
    session: AsyncSession, promo: PromoCode, active: bool
) -> PromoCode:
    promo.is_active = bool(active)
    await session.commit()
    await session.refresh(promo)
    return promo


async def delete_promo(session: AsyncSession, promo: PromoCode) -> None:
    await session.delete(promo)
    await session.commit()
