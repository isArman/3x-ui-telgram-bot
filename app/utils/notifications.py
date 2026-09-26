"""Periodic expiry and low-traffic alerts.

Both alerts are actionable: the message carries a "تمدید اکانت" button wired
to the normal renewal flow (callback `renew_account:<id>`), so a user can
renew straight from the alert.
"""

from datetime import datetime, timedelta

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.user import usage_alert_keyboard
from app.database.models import VPNAccount
from app.services.panel_settings import (
    get_panel_settings,
    is_auto_provisioning_ready,
    xui_client_for_panel,
)
from app.services.traffic_usage import (
    format_gb,
    is_low_traffic,
    parse_client_traffic,
    remaining_traffic_percent,
)
from app.utils.logger import logger

# Alert when this many days (or fewer) remain before expiry.
EXPIRY_WARNING_DAYS = 3


async def check_expiring_accounts(session: AsyncSession, bot: Bot) -> int:
    """Alert accounts close to expiry, once per account, with a renew button."""
    now = datetime.utcnow()
    cutoff = now + timedelta(days=EXPIRY_WARNING_DAYS)

    result = await session.execute(
        select(VPNAccount).where(
            VPNAccount.is_active == True,
            VPNAccount.expires_at > now,
            VPNAccount.expires_at <= cutoff,
            VPNAccount.expiry_notified == False,
        )
    )
    accounts = result.scalars().all()

    notified_count = 0

    for account in accounts:
        try:
            days_left = max((account.expires_at - now).days, 0)

            if days_left <= 0:
                lead = "اکانت شما امروز منقضی می‌شود."
            elif days_left == 1:
                lead = "اکانت شما ۱ روز دیگر منقضی می‌شود."
            else:
                lead = f"اکانت شما {days_left} روز دیگر منقضی می‌شود."

            message = (
                f"{lead}\n\n"
                f"شماره سفارش: #{account.order_id}\n"
                f"تاریخ انقضا: {account.expires_at.strftime('%Y-%m-%d')}\n"
                f"حجم: {account.traffic_limit_gb} گیگابایت\n\n"
                "با دکمه زیر می‌توانید همین‌جا تمدید کنید."
            )

            await bot.send_message(
                chat_id=account.user_id,
                text=message,
                reply_markup=usage_alert_keyboard(account.id),
            )

            account.expiry_notified = True
            notified_count += 1

            logger.info(
                "Sent expiry alert for account %s to user %s",
                account.id,
                account.user_id,
            )

        except Exception as exc:
            logger.error(
                "Failed to send expiry alert for account %s: %s",
                account.id,
                exc,
            )

    if notified_count > 0:
        await session.commit()
        logger.info("Sent %s expiry alerts", notified_count)

    return notified_count


async def check_low_traffic_accounts(session: AsyncSession, bot: Bot) -> int:
    """Alert accounts whose remaining panel traffic is below the threshold."""

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
                        "حجم اکانت شما در حال تمام شدن است.\n\n"
                        f"شماره سفارش: #{account.order_id}\n"
                        f"حجم کل: {format_gb(total_bytes)} GB\n"
                        f"مصرف شده: {format_gb(used_bytes)} GB\n"
                        f"باقی‌مانده: {format_gb(remaining_bytes)} GB "
                        f"(حدود {remaining_pct:.0f}٪)\n\n"
                        "با دکمه زیر می‌توانید همین‌جا تمدید کنید."
                    )

                    await bot.send_message(
                        chat_id=account.user_id,
                        text=message,
                        reply_markup=usage_alert_keyboard(account.id),
                    )
                    account.traffic_low_notified = True
                    notified_count += 1

                    logger.info(
                        "Sent low-traffic alert for account %s to user %s",
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
        logger.error("Low-traffic alert run failed: %s", exc)
        return 0

    if notified_count > 0:
        await session.commit()
        logger.info("Sent %s low-traffic alerts", notified_count)

    return notified_count
