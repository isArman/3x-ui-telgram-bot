"""Telegram Stars payments.

Telegram settles Stars inside the chat, so the payment update arrives at the
bot. The Mini App creates the invoice; this module completes the order when
Telegram confirms the payment.

Every Stars payment is recorded — even one that cannot be delivered — because
Telegram has already taken the money. The admins get a message whenever a
payment needs their attention.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy import select

from app.config.settings import settings
from app.database.models import Order, Payment
from app.database.session import AsyncSessionLocal
from app.services.fulfillment import deliver_paid_order
from app.services.telegram_payments import build_stars_invoice, order_id_from_payload
from app.utils.logger import logger

router = Router()

# Payment status for a Stars payment that could not be delivered.
STATUS_DUPLICATE = "duplicate"


@router.pre_checkout_query()
async def approve_pre_checkout(query) -> None:
    """Telegram asks before charging — check the order, then approve it."""
    order_id = order_id_from_payload(query.invoice_payload)
    if order_id is None:
        await query.answer(ok=False, error_message="سفارش پیدا نشد.")
        return

    async with AsyncSessionLocal() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if not order or order.user_id != query.from_user.id:
            await query.answer(ok=False, error_message="سفارش پیدا نشد.")
            return
        if order.status != "awaiting_stars":
            await query.answer(
                ok=False, error_message="این سفارش در انتظار پرداخت نیست."
            )
            return
        # The invoice amount is the star count — Telegram Stars have no
        # sub-unit, so the amount has to match the price the customer agreed on.
        stars, _rate = await build_stars_invoice(session, order)
        if int(query.total_amount) != stars:
            logger.warning(
                "Stars amount mismatch for order %s: %s vs %s",
                order.id,
                query.total_amount,
                stars,
            )
            await query.answer(
                ok=False, error_message="مبلغ فاکتور تغییر کرده است."
            )
            return

    await query.answer(ok=True)


@router.message(F.successful_payment)
async def stars_paid(message: Message) -> None:
    """Telegram confirmed a Stars payment: record it and deliver the order."""
    info = message.successful_payment
    order_id = order_id_from_payload(info.invoice_payload)
    if order_id is None:
        logger.warning(
            "Stars payment with an unknown payload: %s", info.invoice_payload
        )
        return

    charge_id = info.telegram_payment_charge_id

    async with AsyncSessionLocal() as session:
        order = (
            await session.execute(select(Order).where(Order.id == order_id))
        ).scalar_one_or_none()
        if not order or order.user_id != message.from_user.id:
            logger.warning("Stars payment for an unknown order: %s", order_id)
            await _notify_admins(
                message.bot,
                "پرداخت استارز برای سفارش ناشناخته دریافت شد.\n"
                f"سفارش: #{order_id}\n"
                f"کاربر: {message.from_user.id}\n"
                f"شناسه پرداخت: {charge_id}\n\n"
                "مبلغ از حساب مشتری کسر شده؛ بازپرداخت لازم است.",
            )
            return

        if order.status != "awaiting_stars":
            # Telegram already charged the customer. The order was paid or
            # cancelled in the meantime, so the money has to be refunded — it
            # is still recorded so the admins can find it.
            payment = Payment(
                order_id=order.id,
                user_id=order.user_id,
                receipt_file_id=charge_id or "",
                status=STATUS_DUPLICATE,
                method="stars",
                telegram_charge_id=charge_id,
            )
            session.add(payment)
            await session.commit()
            logger.warning(
                "Duplicate Stars payment for order %s (status %s)",
                order.id,
                order.status,
            )
            await _notify_admins(
                message.bot,
                "پرداخت استارز تکراری دریافت شد.\n"
                f"سفارش: #{order.id}\n"
                f"کاربر: {order.user_id}\n"
                f"وضعیت سفارش: {order.status}\n"
                f"شناسه پرداخت: {charge_id}\n\n"
                "مبلغ از حساب مشتری کسر شده؛ بازپرداخت لازم است.",
            )
            return

        order.status = "paid"
        payment = Payment(
            order_id=order.id,
            user_id=order.user_id,
            receipt_file_id=charge_id or "",
            status="pending",
            method="stars",
            telegram_charge_id=charge_id,
        )
        session.add(payment)
        await session.commit()
        await session.refresh(payment)

    await deliver_paid_order(message.bot, order_id, payment.id)


async def _notify_admins(bot, text: str) -> None:
    """Tell the admins that a payment needs their attention."""
    for admin_id in settings.ADMIN_IDS:
        try:
            await bot.send_message(chat_id=admin_id, text=text)
        except Exception as exc:  # pragma: no cover - network
            logger.warning("Could not notify admin %s: %s", admin_id, exc)
