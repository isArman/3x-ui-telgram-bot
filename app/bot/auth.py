"""Authorization helpers for admin-only bot actions."""

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.config.settings import settings


def is_admin(user_id: int) -> bool:
    return user_id in settings.ADMIN_IDS


class AdminFilter(BaseFilter):
    async def __call__(self, event: TelegramObject) -> bool:
        user = getattr(event, "from_user", None)
        return user is not None and is_admin(user.id)


async def deny_non_admin_callback(callback: CallbackQuery, text: str = "دسترسی ندارید!") -> None:
    await callback.answer(text, show_alert=True)


async def deny_non_admin_message(message: Message) -> None:
    await message.answer("⛔ شما دسترسی ادمین ندارید.")
