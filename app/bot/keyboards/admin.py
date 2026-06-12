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


def panel_config_keyboard(auto_create: bool, provision_mode: str) -> InlineKeyboardMarkup:
    """3x-ui panel configuration keyboard for admins."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🌐 URL عمومی", callback_data="panel:set_public_url"),
        InlineKeyboardButton(text="🔧 URL API", callback_data="panel:set_url"),
    )
    builder.row(
        InlineKeyboardButton(text="👤 نام کاربری", callback_data="panel:set_username"),
        InlineKeyboardButton(text="🔑 رمز عبور", callback_data="panel:set_password"),
    )
    builder.row(
        InlineKeyboardButton(text="📡 Inbound ID", callback_data="panel:set_inbound"),
    )
    mode_label = "🌍 Remote (worker)" if provision_mode == "remote" else "🔗 Direct"
    builder.row(
        InlineKeyboardButton(text=f"حالت: {mode_label}", callback_data="panel:toggle_mode"),
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
