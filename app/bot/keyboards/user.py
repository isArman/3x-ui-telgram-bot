from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from typing import List, Dict, Any


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Main menu keyboard"""
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="📦 خرید پلن"),
        KeyboardButton(text="🎨 پلن سفارشی")
    )
    builder.row(
        KeyboardButton(text="📋 سفارش‌های من")
    )
    return builder.as_markup(resize_keyboard=True)


def plans_keyboard(plans: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    """Plans selection keyboard"""
    builder = InlineKeyboardBuilder()
    
    for plan in plans:
        builder.row(
            InlineKeyboardButton(
                text=f"{plan['name']} - {plan['price']:,} تومان",
                callback_data=f"plan:{plan['id']}"
            )
        )
    
    builder.row(
        InlineKeyboardButton(text="🔙 بازگشت", callback_data="back_to_main")
    )
    
    return builder.as_markup()


def confirm_order_keyboard() -> InlineKeyboardMarkup:
    """Order confirmation keyboard"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ تایید", callback_data="confirm_order"),
        InlineKeyboardButton(text="❌ لغو", callback_data="cancel_order")
    )
    return builder.as_markup()


def cancel_keyboard() -> ReplyKeyboardMarkup:
    """Cancel keyboard"""
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="❌ لغو"))
    return builder.as_markup(resize_keyboard=True)
