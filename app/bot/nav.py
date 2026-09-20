"""Shared reply-nav helpers for Back / Cancel."""

from __future__ import annotations

from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot.constants import (
    ADMIN_MENU_TEXT,
    BACK_BUTTON,
    CANCEL_BUTTON,
    FLOW_NAV_BUTTONS,
)
from app.bot.keyboards.admin import admin_menu_keyboard
from app.bot.keyboards.user import main_menu_keyboard
from app.config.settings import settings


def is_nav_text(text: str | None) -> bool:
    return bool(text) and text in FLOW_NAV_BUTTONS


async def exit_admin_fsm(
    message: Message,
    state: FSMContext,
    *,
    notice: str = "لغو شد.",
    show_admin_root: bool = True,
) -> None:
    """Clear FSM, restore user main reply keyboard, optionally show admin menu."""
    await state.clear()
    await message.answer(
        notice,
        reply_markup=main_menu_keyboard(
            is_admin=message.from_user.id in settings.ADMIN_IDS
        ),
    )
    if show_admin_root:
        await message.answer(ADMIN_MENU_TEXT, reply_markup=admin_menu_keyboard())


async def restore_user_main_keyboard(message: Message, text: str) -> None:
    await message.answer(
        text,
        reply_markup=main_menu_keyboard(
            is_admin=message.from_user.id in settings.ADMIN_IDS
        ),
    )


# Re-export for callers that already import CANCEL/BACK from constants
__all__ = [
    "BACK_BUTTON",
    "CANCEL_BUTTON",
    "FLOW_NAV_BUTTONS",
    "is_nav_text",
    "exit_admin_fsm",
    "restore_user_main_keyboard",
]
