from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from app.bot.constants import CANCEL_BUTTON


def admin_cancel_keyboard() -> ReplyKeyboardMarkup:
    """Single cancel button for admin FSM flows."""
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text=CANCEL_BUTTON))
    return builder.as_markup(resize_keyboard=True)


def payment_review_keyboard(payment_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ تایید", callback_data=f"approve_payment:{payment_id}"),
        InlineKeyboardButton(text="❌ رد", callback_data=f"reject_payment:{payment_id}"),
    )
    return builder.as_markup()


def admin_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔗 پنل 3x-ui", callback_data="admin:panel"))
    builder.row(InlineKeyboardButton(text="🗂 مدیریت کانفیگ‌ها", callback_data="admin:configs"))
    builder.row(InlineKeyboardButton(text="📊 داشبورد", callback_data="admin:dashboard"))
    builder.row(InlineKeyboardButton(text="📋 پرداخت‌های در انتظار", callback_data="admin:pending"))
    return builder.as_markup()


def panel_menu_keyboard(provisioning_mode: str, is_verified: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    mode_label = "🤖 خودکار" if provisioning_mode == "auto" else "📦 انبار دستی"
    builder.row(
        InlineKeyboardButton(
            text=f"حالت ارسال: {mode_label}",
            callback_data="admin:panel:mode",
        )
    )
    builder.row(
        InlineKeyboardButton(text="⚙️ تنظیم اتصال پنل", callback_data="admin:panel:setup")
    )
    if is_verified:
        builder.row(
            InlineKeyboardButton(
                text="📡 بروزرسانی Inboundها",
                callback_data="admin:panel:inbounds",
            )
        )
        builder.row(
            InlineKeyboardButton(
                text="🔍 تست اتصال",
                callback_data="admin:panel:test",
            )
        )
    builder.row(InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:menu"))
    return builder.as_markup()


def inbound_select_keyboard(
    inbounds: list,
    selected_ids: set[int],
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for ib in inbounds:
        ib_id = ib["id"]
        mark = "✅" if ib_id in selected_ids else "⬜"
        remark = (ib.get("remark") or f"#{ib_id}").strip() or f"#{ib_id}"
        protocol = ib.get("protocol") or "?"
        label = f"{mark} {remark} ({protocol})"
        if len(label) > 60:
            label = label[:57] + "..."
        builder.row(
            InlineKeyboardButton(
                text=label,
                callback_data=f"admin:panel:inbound_toggle:{ib_id}",
            )
        )
    builder.row(
        InlineKeyboardButton(text="💾 ذخیره", callback_data="admin:panel:inbounds_save")
    )
    builder.row(
        InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:panel")
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
    builder.row(
        InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:menu"),
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
