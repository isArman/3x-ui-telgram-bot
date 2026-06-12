import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app.config.settings import settings
from app.database.session import init_db
from app.bot.handlers import user, admin
from app.utils.logger import logger as app_logger
from app.utils.scheduler import run_notification_scheduler
from app.worker.api import start_worker_api
from app.worker.processor import run_provision_job_processor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def on_startup():
    """Startup hook"""
    logger.info("Initializing database...")
    await init_db()
    logger.info("Database initialized!")


async def on_shutdown():
    """Shutdown hook"""
    logger.info("Bot shutting down...")


async def main():
    """Main bot runner"""
    settings.validate()

    bot = Bot(token=settings.BOT_TOKEN)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    dp.include_router(user.router)
    dp.include_router(admin.router)

    await on_startup()

    background_tasks = [
        asyncio.create_task(run_notification_scheduler(bot)),
        asyncio.create_task(run_provision_job_processor(bot)),
    ]
    app_logger.info("Notification scheduler started")
    app_logger.info("Provision job processor started")

    worker_runner = None
    if settings.WORKER_SECRET:
        worker_runner = await start_worker_api()
        app_logger.info("Worker API started for remote provisioning")
    else:
        app_logger.warning("WORKER_SECRET not set — remote worker API disabled")

    try:
        logger.info("Starting bot polling...")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        for task in background_tasks:
            task.cancel()
        if worker_runner:
            await worker_runner.cleanup()
        app_logger.info("Background tasks stopped")
        await on_shutdown()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
