import asyncio
from datetime import datetime
from aiogram import Bot
from app.database.session import AsyncSessionLocal
from app.utils.notifications import check_expiring_accounts, check_low_traffic_accounts
from app.utils.logger import logger


async def run_notification_scheduler(bot: Bot):
    """Run periodic tasks for notifications"""
    
    logger.info("Notification scheduler started")
    
    while True:
        try:
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

            # Check every 6 hours (after the first run on startup)
            await asyncio.sleep(6 * 60 * 60)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Error in notification scheduler: {e}")
            # Wait 1 hour before retrying on error
            await asyncio.sleep(60 * 60)
