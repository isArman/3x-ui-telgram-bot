import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot.handlers import admin, panel_admin, user, wallet
from app.bot.middleware import BlockedUserMiddleware
from app.config.settings import settings
from app.database.session import init_db
from app.utils.logger import logger as app_logger
from app.utils.scheduler import run_notification_scheduler

logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL, logging.INFO))
logger = logging.getLogger(__name__)


async def on_startup():
    logger.info("Initializing database...")
    await init_db()
    logger.info("Database initialized!")


async def on_shutdown():
    logger.info("Bot shutting down...")


async def main():
    settings.validate()

    bot = Bot(token=settings.BOT_TOKEN)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    dp.message.middleware(BlockedUserMiddleware())
    dp.callback_query.middleware(BlockedUserMiddleware())

    dp.include_router(panel_admin.router)
    dp.include_router(admin.router)
    dp.include_router(wallet.router)
    dp.include_router(user.router)

    await on_startup()
    app_logger.info("Bot started with %d admin(s) configured", len(settings.ADMIN_IDS))

    scheduler_task = asyncio.create_task(run_notification_scheduler(bot))
    app_logger.info("Notification scheduler started")

    try:
        logger.info("Starting bot polling...")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass
        app_logger.info("Notification scheduler stopped")
        await on_shutdown()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
