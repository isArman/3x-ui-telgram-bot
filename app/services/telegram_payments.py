"""Telegram Stars (XTR) payments.

Stars are settled by Telegram itself, so no gateway account, no webhook domain
and no SSL are needed — a sale is confirmed the moment the customer pays. The
customer pays a small premium (Telegram keeps a fee), so this sits next to the
card-to-card option rather than replacing it.
"""

from __future__ import annotations

import math
from typing import Optional

from aiogram import Bot
from aiogram.types import LabeledPrice
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Order
from app.services.bot_settings import get_bot_settings

XTR_CURRENCY = "XTR"
# Telegram's hard limit on the invoice payload.
_MAX_PAYLOAD_BYTES = 64


async def get_stars_rate(session: AsyncSession) -> int:
    """How many toman one star is worth (admin configurable)."""
    row = await get_bot_settings(session)
    return max(int(row.stars_rate or 50), 1)


def price_to_stars(price_toman: int, rate: int) -> int:
    """Convert a toman price into whole stars, rounding up."""
    rate = max(int(rate or 1), 1)
    price = max(int(price_toman or 0), 0)
    if price <= 0:
        return 0
    return max(1, math.ceil(price / rate))


def stars_to_toman(stars: int, rate: int) -> int:
    rate = max(int(rate or 1), 1)
    return max(int(stars or 0), 0) * rate


def invoice_payload(order_id: int) -> str:
    """Payload echoed back by Telegram; keep it short and opaque."""
    return f"o{order_id}"


def order_id_from_payload(payload: str) -> Optional[int]:
    text = (payload or "").strip()
    if text.startswith("o") and text[1:].isdigit():
        return int(text[1:])
    if text.isdigit():
        return int(text)
    return None


async def send_stars_invoice(
    bot: Bot,
    chat_id: int,
    order: Order,
    *,
    stars: int,
    plan_name: str = "",
) -> None:
    """Send the Telegram Stars invoice for an order."""
    await bot.send_invoice(
        chat_id=chat_id,
        title=plan_name or f"اشتراک #{order.id}",
        description=f"اشتراک {order.days} روزه با {order.traffic_gb} گیگابایت",
        payload=invoice_payload(order.id),
        provider_token="",  # empty for Telegram Stars
        currency=XTR_CURRENCY,
        prices=[
            LabeledPrice(
                label=f"{stars} ⭐",
                amount=stars * 100,  # Telegram wants the smallest unit
            )
        ],
    )


async def build_stars_invoice(
    session: AsyncSession, order: Order, plan_name: str = ""
) -> tuple[int, int]:
    """(stars, rate) for an order."""
    rate = await get_stars_rate(session)
    return price_to_stars(order.price, rate), rate
