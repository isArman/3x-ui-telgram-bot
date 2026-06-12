from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def payment_review_keyboard(payment_id: int) -> InlineKeyboardMarkup:
    """Payment review keyboard for admins"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ تایید", callback_data=f"approve_payment:{payment_id}"),
        InlineKeyboardButton(text="❌ رد", callback_data=f"reject_payment:{payment_id}")
    )
    return builder.as_markup()


def panel_config_keyboard(auto_create: bool) -> InlineKeyboardMarkup:
    """3x-ui panel configuration keyboard for admins."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🌐 URL پنل", callback_data="panel:set_url"),
        InlineKeyboardButton(text="👤 نام کاربری", callback_data="panel:set_username"),
    )
    builder.row(
        InlineKeyboardButton(text="🔑 رمز عبور", callback_data="panel:set_password"),
        InlineKeyboardButton(text="📡 Inbound ID", callback_data="panel:set_inbound"),
    )
    builder.row(
        InlineKeyboardButton(
            text="🔄 تست اتصال",
            callback_data="panel:test_connection",
        )
    )
    toggle_text = "⏸ غیرفعال کردن ساخت خودکار" if auto_create else "▶️ فعال کردن ساخت خودکار"
    builder.row(
        InlineKeyboardButton(text=toggle_text, callback_data="panel:toggle_auto")
    )
    return builder.as_markup()
