import asyncio
from datetime import datetime, timedelta

from aiogram import Bot
from sqlalchemy import select

from app.config.texts import get_text
from app.database.models import Order, Payment, ProvisionJob, VPNAccount
from app.database.session import AsyncSessionLocal
from app.utils.logger import logger


async def process_completed_provision_jobs(bot: Bot) -> None:
    """Finalize completed remote jobs: save account, notify user and admin."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ProvisionJob)
            .where(
                ProvisionJob.status == "completed",
                ProvisionJob.notified.is_(False),
            )
            .order_by(ProvisionJob.completed_at.asc())
        )
        jobs = result.scalars().all()

        for job in jobs:
            payment_result = await session.execute(select(Payment).where(Payment.id == job.payment_id))
            payment = payment_result.scalar_one_or_none()
            order_result = await session.execute(select(Order).where(Order.id == job.order_id))
            order = order_result.scalar_one_or_none()

            if not payment or not order or not job.subscription_url:
                job.status = "failed"
                job.error_message = job.error_message or "missing payment/order/subscription"
                job.notified = True
                continue

            payment.status = "approved"
            payment.reviewed_at = datetime.utcnow()
            payment.reviewed_by = job.admin_id
            order.status = "completed"

            vpn_account = VPNAccount(
                order_id=order.id,
                user_id=order.user_id,
                xui_client_id=job.xui_client_id or "remote",
                subscription_path=job.subscription_url,
                expires_at=datetime.utcnow() + timedelta(days=order.days),
                traffic_limit_gb=order.traffic_gb,
                is_active=True,
            )
            session.add(vpn_account)

            try:
                await bot.send_message(
                    chat_id=order.user_id,
                    text=get_text("payment_approved", subscription_url=job.subscription_url),
                )
                await bot.send_message(
                    chat_id=job.admin_id,
                    text=(
                        "✅ ساخت خودکار (remote worker) انجام شد!\n\n"
                        f"👤 کاربر: {order.user_id}\n"
                        f"🔗 لینک: {job.subscription_url}"
                    ),
                )
            except Exception as exc:
                logger.error(f"Failed to notify for job {job.id}: {exc}")

            job.notified = True
            await session.commit()
            logger.info(f"Finalized provision job {job.id}")


async def notify_failed_provision_jobs(bot: Bot) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ProvisionJob)
            .where(
                ProvisionJob.status == "failed",
                ProvisionJob.notified.is_(False),
            )
        )
        jobs = result.scalars().all()

        for job in jobs:
            try:
                await bot.send_message(
                    chat_id=job.admin_id,
                    text=(
                        "❌ ساخت خودکار (remote worker) ناموفق بود.\n\n"
                        f"🆔 Job #{job.id}\n"
                        f"👤 کاربر: {job.user_id}\n"
                        f"⚠️ خطا: {job.error_message or 'نامشخص'}\n\n"
                        "لطفاً لینک اشتراک را دستی ارسال کنید."
                    ),
                )
            except Exception as exc:
                logger.error(f"Failed to notify admin about failed job {job.id}: {exc}")

            job.notified = True
            await session.commit()


async def run_provision_job_processor(bot: Bot) -> None:
    while True:
        try:
            await process_completed_provision_jobs(bot)
            await notify_failed_provision_jobs(bot)
        except Exception as exc:
            logger.error(f"Provision job processor error: {exc}")
        await asyncio.sleep(5)
