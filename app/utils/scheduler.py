"""The background jobs of the shop.

One loop runs everything: the expiry and low-traffic alerts for the customers
and the cleanup of abandoned orders. The jobs are cheap and run on a small
server, so they share a single loop instead of one task each.
"""

from __future__ import annotations

import asyncio
import time

from aiogram import Bot

from app.database.session import AsyncSessionLocal
from app.services.orders_cleanup import cancel_stale_orders
from app.utils.logger import logger
from app.utils.notifications import (
    check_expiring_accounts,
    check_low_traffic_accounts,
)

# The alert jobs run every six hours.
ALERT_INTERVAL_SECONDS = 6 * 60 * 60

# The loop wakes up every hour: the order cleanup runs on every tick.
TICK_SECONDS = 60 * 60


async def run_alerts(bot: Bot) -> None:
    """Alert the customers whose subscription is about to end or nearly full."""
    async with AsyncSessionLocal() as session:
        expiry_count = await check_expiring_accounts(session, bot)
        traffic_count = await check_low_traffic_accounts(session, bot)
        total = expiry_count + traffic_count
        if total > 0:
            logger.info(
                "Notification check completed: %s expiry, %s traffic",
                expiry_count,
                traffic_count,
            )


async def run_cleanup() -> None:
    """Cancel the orders nobody paid for."""
    async with AsyncSessionLocal() as session:
        cancelled = await cancel_stale_orders(session)
        if cancelled:
            logger.info("Cancelled %s abandoned order(s)", cancelled)


async def run_notification_scheduler(bot: Bot) -> None:
    """Run the periodic jobs until the bot stops."""
    logger.info("Background scheduler started")
    last_alerts = 0.0

    while True:
        now = time.monotonic()

        try:
            await run_cleanup()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Cleanup job failed: %s", exc)

        if now - last_alerts >= ALERT_INTERVAL_SECONDS:
            try:
                await run_alerts(bot)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Alert job failed: %s", exc)
            last_alerts = now

        await asyncio.sleep(TICK_SECONDS)
