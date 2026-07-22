"""Bot middleware."""

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.bot.auth import is_admin
from app.database.session import AsyncSessionLocal
from app.services.users import is_user_blocked


class BlockedUserMiddleware(BaseMiddleware):
    """Reject interactions from blocked users (admins are always allowed)."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user and not is_admin(user.id):
            async with AsyncSessionLocal() as session:
                if await is_user_blocked(session, user.id):
                    if isinstance(event, Message):
                        await event.answer("⛔ دسترسی شما مسدود شده است.")
                    elif isinstance(event, CallbackQuery):
                        await event.answer("دسترسی مسدود", show_alert=True)
                    return None
        return await handler(event, data)
