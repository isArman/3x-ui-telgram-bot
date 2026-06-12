from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def payment_review_keyboard(payment_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ تایید", callback_data=f"approve_payment:{payment_id}"),
        InlineKeyboardButton(text="❌ رد", callback_data=f"reject_payment:{payment_id}"),
    )
    return builder.as_markup()


def configs_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📋 موجودی پلن‌ها", callback_data="configs:stock"),
    )
    builder.row(
        InlineKeyboardButton(text="➕ افزودن کانفیگ", callback_data="configs:add"),
    )
    return builder.as_markup()


def plan_select_keyboard(plans, prefix: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for plan in plans:
        builder.row(
            InlineKeyboardButton(
                text=plan["name"],
                callback_data=f"{prefix}:{plan['id']}",
            )
        )
    builder.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="configs:menu"))
    return builder.as_markup()
