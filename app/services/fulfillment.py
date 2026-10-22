"""Deliver a paid order to the customer.

Every payment route ends here: the wallet, Telegram Stars, a card transfer an
admin approved, and a free trial. The order is provisioned through 3x-ui, falls
back to the manual inventory, and asks the admins for a link when neither is
possible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from aiogram import Bot
from sqlalchemy import select

from app.config.settings import settings
from app.config.texts import get_text
from app.database.models import Order, Payment
from app.database.session import AsyncSessionLocal
from app.services.order_fulfillment import fulfill_paid_order
from app.services.plans_catalog import get_plan
from app.utils.logger import logger

KIND_NEW = "new"
KIND_RENEWAL = "renewal"
KIND_MANUAL = "manual"


@dataclass
class Delivery:
    """What happened to a paid order."""

    kind: str
    order_id: int
    user_id: int
    text: Optional[str] = None


async def deliver_paid_order(
    bot: Bot,
    order_id: int,
    payment_id: int,
) -> Delivery:
    """
    Provision and deliver a paid order.

    Never raises: a failure is logged and handed to the admins, because the
    customer has already paid.
    """
    try:
        async with AsyncSessionLocal() as session:
            order = (
                await session.execute(select(Order).where(Order.id == order_id))
            ).scalar_one_or_none()
            payment = (
                await session.execute(select(Payment).where(Payment.id == payment_id))
            ).scalar_one_or_none()
            if not order or not payment:
                logger.warning("Cannot deliver order %s: missing rows", order_id)
                return Delivery(kind=KIND_MANUAL, order_id=order_id, user_id=0)

            plan = await get_plan(session, order.plan_id) if order.plan_id else None
            fulfill = await fulfill_paid_order(
                session, payment, order, None, plan["name"] if plan else None
            )

            if fulfill.kind in ("new_auto", "new_inventory") and fulfill.config_text:
                text = get_text(
                    "payment_approved", subscription_url=fulfill.config_text
                )
                await _send(bot, order.user_id, text)
                return Delivery(
                    kind=KIND_NEW,
                    order_id=order.id,
                    user_id=order.user_id,
                    text=fulfill.config_text,
                )

            if fulfill.kind in ("renewal_auto", "renewal_db") and fulfill.vpn_account:
                text = get_text(
                    "renewal_approved",
                    days=order.days,
                    traffic=order.traffic_gb,
                    expires_at=fulfill.vpn_account.expires_at.strftime("%Y-%m-%d"),
                    subscription_url=fulfill.vpn_account.subscription_path,
                )
                await _send(bot, order.user_id, text)
                return Delivery(
                    kind=KIND_RENEWAL,
                    order_id=order.id,
                    user_id=order.user_id,
                    text=fulfill.vpn_account.subscription_path,
                )

            await notify_admins_manual(bot, payment, order)
            await _send(bot, order.user_id, get_text("order_awaiting_admin"))
            return Delivery(
                kind=KIND_MANUAL, order_id=order.id, user_id=order.user_id
            )

    except Exception as exc:
        logger.error("Delivery of order %s failed: %s", order_id, exc, exc_info=True)
        await _notify_admins_failure(bot, order_id, exc)
        return Delivery(kind=KIND_MANUAL, order_id=order_id, user_id=0)


async def notify_admins_manual(
    bot: Bot, payment: Payment, order: Order
) -> None:
    """Tell the admins that an order needs a subscription link by hand."""
    wallet_debit = int(order.wallet_debit or 0)
    lines = [
        "یک سفارش به لینک دستی نیاز دارد.",
        f"کاربر: {order.user_id}",
        f"سفارش: #{order.id}",
        f"{order.days} روز | {order.traffic_gb} گیگابایت",
        f"مبلغ: {order.price:,} تومان",
    ]
    if wallet_debit:
        lines.append(f"از کیف پول: {wallet_debit:,} تومان")
    lines.append(admin_panel_hint())

    for admin_id in settings.ADMIN_IDS:
        await _send(bot, admin_id, "\n".join(lines))


async def _notify_admins_failure(bot: Bot, order_id: int, exc: Exception) -> None:
    text = (
        "فعال‌سازی خودکار یک سفارش با خطا مواجه شد.\n"
        f"سفارش: #{order_id}\n"
        f"خطا: {exc}\n\n"
        + admin_panel_hint()
    )
    for admin_id in settings.ADMIN_IDS:
        await _send(bot, admin_id, text)


def admin_panel_hint() -> str:
    """A pointer to the Mini App admin panel."""
    if not settings.MINIAPP_URL:
        return ""
    return f"پنل مدیریت: {settings.MINIAPP_URL}?tab=admin"


async def _send(bot: Bot, chat_id: int, text: str) -> None:
    try:
        await bot.send_message(chat_id=chat_id, text=text)
    except Exception as exc:
        logger.warning("Could not deliver a message to %s: %s", chat_id, exc)
