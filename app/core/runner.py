import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app.config.settings import settings
from app.database.session import init_db
from app.bot.handlers import user, admin

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
    # Validate settings
    settings.validate()
    
    # Initialize bot and dispatcher
    bot = Bot(token=settings.BOT_TOKEN)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    
    # Register routers
    dp.include_router(user.router)
    dp.include_router(admin.router)
    
    # Startup
    await on_startup()
    
    try:
        logger.info("Starting bot polling...")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await on_shutdown()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
