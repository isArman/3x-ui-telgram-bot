"""The bot is the Mini App's launcher.

The customer interface is the Telegram Mini App; the bot only opens it, binds
a referral code from the ``/start`` deep link, and handles the payment
updates Telegram sends back (Telegram Stars).
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    WebAppInfo,
)

from app.config.settings import settings
from app.config.texts import get_text
from app.database.session import AsyncSessionLocal
from app.services.referral import try_bind_referrer
from app.services.users import get_or_create_user
from app.utils.logger import logger

router = Router()

# Telegram deep-link payloads are limited to 64 characters.
MAX_PAYLOAD_LENGTH = 64


def mini_app_keyboard() -> ReplyKeyboardMarkup:
    """The reply keyboard that opens the Mini App."""
    url = settings.MINIAPP_URL
    if not url:
        return ReplyKeyboardRemove()
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🛒 فروشگاه", web_app=WebAppInfo(url=url))]],
        resize_keyboard=True,
    )


def start_payload(message: Message) -> str:
    """The ``/start <payload>`` deep-link code, or an empty string."""
    text = (message.text or "").strip()
    if not text.startswith("/start"):
        return ""
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        return ""
    payload = parts[1].strip()
    if len(payload) > MAX_PAYLOAD_LENGTH:
        return ""
    return payload


async def bind_referral(message: Message, code: str) -> None:
    """
    Bind the referrer of a brand-new customer.

    The referral link is ``https://t.me/<bot>?start=<code>``. The code is only
    honoured for a customer that does not exist yet, so a referral cannot be
    re-pointed later.
    """
    if not code or message.from_user is None:
        return
    try:
        async with AsyncSessionLocal() as session:
            user, created = await get_or_create_user(session, message.from_user)
            if created:
                bound = await try_bind_referrer(
                    session, user, code, is_new_user=True
                )
                if bound:
                    logger.info(
                        "Bound referral %s for user %s", code, message.from_user.id
                    )
            await session.commit()
    except Exception as exc:  # pragma: no cover - database
        logger.error("Could not bind the referral %s: %s", code, exc)


async def open_shop(message: Message) -> None:
    """Open the Mini App, binding a referral code when one was given."""
    code = start_payload(message)
    if code:
        await bind_referral(message, code)
    await message.answer(get_text("start"), reply_markup=mini_app_keyboard())


@router.message(CommandStart(deep_link=True))
async def start_with_code(message: Message) -> None:
    """The customer came through a referral link."""
    await open_shop(message)


@router.message(CommandStart())
async def start(message: Message) -> None:
    """Open the Mini App."""
    await open_shop(message)


@router.message(Command("app"))
async def open_app(message: Message) -> None:
    """Open the Mini App again."""
    await message.answer(get_text("start"), reply_markup=mini_app_keyboard())


@router.callback_query(F.data == "open_app")
async def open_app_callback(callback: CallbackQuery) -> None:
    await callback.answer("از دکمه فروشگاه استفاده کنید.")
