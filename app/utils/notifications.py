from datetime import datetime, timedelta

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import VPNAccount
from app.services.panel_settings import get_panel_settings, is_auto_provisioning_ready, xui_client_for_panel
from app.services.traffic_usage import (
    format_gb,
    is_low_traffic,
    parse_client_traffic,
    remaining_traffic_percent,
)
from app.utils.logger import logger


async def check_expiring_accounts(session: AsyncSession, bot: Bot) -> int:
    """Check for accounts expiring soon and send notifications."""

    now = datetime.utcnow()
    three_days_ahead = now + timedelta(days=3)

    result = await session.execute(
        select(VPNAccount).where(
            VPNAccount.is_active == True,
            VPNAccount.expires_at > now,
            VPNAccount.expires_at <= three_days_ahead,
            VPNAccount.expiry_notified == False,
        )
    )
    accounts = result.scalars().all()

    notified_count = 0

    for account in accounts:
        try:
            days_left = (account.expires_at - now).days

            message = (
                f"⚠️ هشدار انقضای اکانت\n\n"
                f"اکانت شما (سفارش #{account.order_id}) در {days_left} روز دیگر منقضی می‌شود.\n\n"
                f"📅 تاریخ انقضا: {account.expires_at.strftime('%Y-%m-%d')}\n"
                f"📊 حجم: {account.traffic_limit_gb} گیگابایت\n\n"
                f"برای تمدید از «💳 اکانت‌های من» استفاده کنید."
            )

            await bot.send_message(chat_id=account.user_id, text=message)

            account.expiry_notified = True
            notified_count += 1

            logger.info(
                "Sent expiry notification for account %s to user %s",
                account.id,
                account.user_id,
            )

        except Exception as exc:
            logger.error(
                "Failed to send expiry notification for account %s: %s",
                account.id,
                exc,
            )

    if notified_count > 0:
        await session.commit()
        logger.info("Sent %s expiry notifications", notified_count)

    return notified_count


async def check_low_traffic_accounts(session: AsyncSession, bot: Bot) -> int:
    """Notify users when panel traffic drops below 10% remaining."""

    panel = await get_panel_settings(session)
    if not is_auto_provisioning_ready(panel):
        return 0

    now = datetime.utcnow()
    result = await session.execute(
        select(VPNAccount).where(
            VPNAccount.is_active == True,
            VPNAccount.expires_at > now,
            VPNAccount.traffic_low_notified == False,
        )
    )
    accounts = result.scalars().all()
    if not accounts:
        return 0

    notified_count = 0

    try:
        async with xui_client_for_panel(panel) as client:
            for account in accounts:
                try:
                    detail = await client.get_client(str(account.user_id))
                    if not detail:
                        continue

                    parsed = parse_client_traffic(detail)
                    if parsed is None:
                        continue

                    total_bytes, used_bytes = parsed
                    if not is_low_traffic(total_bytes, used_bytes):
                        continue

                    remaining_bytes = max(total_bytes - used_bytes, 0)
                    remaining_pct = remaining_traffic_percent(total_bytes, used_bytes)

                    message = (
                        f"⚠️ هشدار اتمام حجم\n\n"
                        f"اکانت شما (سفارش #{account.order_id}) کمتر از ۱۰٪ حجم باقی‌مانده دارد.\n\n"
                        f"📊 حجم کل: {format_gb(total_bytes)} GB\n"
                        f"📉 مصرف شده: {format_gb(used_bytes)} GB\n"
                        f"💾 باقی‌مانده: {format_gb(remaining_bytes)} GB "
                        f"(~{remaining_pct:.1f}%)\n\n"
                        f"برای تمدید از «💳 اکانت‌های من» استفاده کنید."
                    )

                    await bot.send_message(chat_id=account.user_id, text=message)
                    account.traffic_low_notified = True
                    notified_count += 1

                    logger.info(
                        "Sent low-traffic notification for account %s to user %s",
                        account.id,
                        account.user_id,
                    )

                except Exception as exc:
                    logger.error(
                        "Failed low-traffic check for account %s: %s",
                        account.id,
                        exc,
                    )
    except Exception as exc:
        logger.error("Low-traffic notification run failed: %s", exc)
        return 0

    if notified_count > 0:
        await session.commit()
        logger.info("Sent %s low-traffic notifications", notified_count)

    return notified_count
