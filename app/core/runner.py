import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot import launcher, payments
from app.config.settings import settings
from app.database.session import init_db
from app.utils.logger import logger as app_logger
from app.utils.scheduler import run_notification_scheduler
from app.web.server import start_web_server

logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL, logging.INFO))
logger = logging.getLogger(__name__)


async def on_startup():
    logger.info("Initializing database...")
    await init_db()
    logger.info("Database initialized!")


async def setup_bot_menu(bot: Bot) -> None:
    """Point the chat menu button at the Mini App and list the main commands."""
    from aiogram.types import (
        BotCommand,
        MenuButtonWebApp,
        WebAppInfo,
    )

    # Only the commands the bot actually answers are advertised.
    commands = [
        BotCommand(command="start", description="باز کردن فروشگاه"),
        BotCommand(command="app", description="باز کردن فروشگاه"),
    ]

    try:
        await bot.set_my_commands(commands)
        if settings.MINIAPP_URL:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="فروشگاه",
                    web_app=WebAppInfo(url=settings.MINIAPP_URL),
                )
            )
        logger.info("Bot menu points at the Mini App")
    except Exception as exc:
        logger.warning("Could not set the bot menu: %s", exc)


async def on_shutdown():
    logger.info("Bot shutting down...")


async def main():
    settings.validate()

    bot = Bot(token=settings.BOT_TOKEN)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    dp.include_router(payments.router)
    dp.include_router(launcher.router)

    await on_startup()
    app_logger.info("Bot started with %d admin(s) configured", len(settings.ADMIN_IDS))

    await setup_bot_menu(bot)

    web_runner = await start_web_server(
        bot, host=settings.WEB_HOST, port=settings.WEB_PORT
    )

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
        await web_runner.cleanup()
        await on_shutdown()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
