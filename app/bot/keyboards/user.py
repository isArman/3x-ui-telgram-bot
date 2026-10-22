from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from typing import List, Dict, Any

from app.bot.constants import (
    BACK_BUTTON,
    BTN_ADMIN_PANEL,
    BTN_BUY_PLAN,
    BTN_CUSTOM_PLAN,
    BTN_MY_ACCOUNTS,
    BTN_MY_ORDERS,
    BTN_REFERRAL,
    BTN_TOP_UP,
    BTN_WALLET,
    CANCEL_BUTTON,
    callback_data_fits,
)


def main_menu_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    """Main menu keyboard"""
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text=BTN_BUY_PLAN),
        KeyboardButton(text=BTN_CUSTOM_PLAN),
    )
    builder.row(
        KeyboardButton(text=BTN_MY_ORDERS),
        KeyboardButton(text=BTN_MY_ACCOUNTS),
    )
    builder.row(
        KeyboardButton(text=BTN_WALLET),
        KeyboardButton(text=BTN_REFERRAL),
    )
    if is_admin:
        builder.row(KeyboardButton(text=BTN_ADMIN_PANEL))
    return builder.as_markup(resize_keyboard=True)


def wallet_keyboard() -> ReplyKeyboardMarkup:
    """Wallet home reply keyboard."""
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text=BTN_TOP_UP))
    builder.row(
        KeyboardButton(text=BACK_BUTTON),
        KeyboardButton(text=CANCEL_BUTTON),
    )
    return builder.as_markup(resize_keyboard=True)


def confirm_topup_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="تایید", callback_data="confirm_topup"),
    )
    builder.row(
        InlineKeyboardButton(text=BACK_BUTTON, callback_data="back_topup_amount"),
        InlineKeyboardButton(text=CANCEL_BUTTON, callback_data="cancel_topup"),
    )
    return builder.as_markup()


def wallet_pay_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="بله، از کیف پول",
            callback_data="wallet_pay:yes",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="خیر، فقط کارت",
            callback_data="wallet_pay:no",
        ),
    )
    builder.row(
        InlineKeyboardButton(text=CANCEL_BUTTON, callback_data="cancel_order"),
    )
    return builder.as_markup()


def flow_nav_keyboard() -> ReplyKeyboardMarkup:
    """Back / cancel keyboard for multi-step user flows."""
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text=BACK_BUTTON),
        KeyboardButton(text=CANCEL_BUTTON),
    )
    return builder.as_markup(resize_keyboard=True)


def plans_keyboard(plans: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    """Plans selection keyboard"""
    builder = InlineKeyboardBuilder()

    for plan in plans:
        data = f"plan:{plan['id']}"
        if not callback_data_fits(data):
            continue
        builder.row(
            InlineKeyboardButton(
                text=f"{plan['name']} - {plan['price']:,} تومان",
                callback_data=data,
            )
        )

    builder.row(
        InlineKeyboardButton(text=BACK_BUTTON, callback_data="back:main"),
    )

    return builder.as_markup()


def confirm_order_keyboard(
    back_callback: str = "back:plans", *, has_promo: bool = False
) -> InlineKeyboardMarkup:
    """Order confirmation keyboard."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="تایید", callback_data="confirm_order"),
    )
    if not has_promo:
        builder.row(
            InlineKeyboardButton(
                text="🏷️ کد پرومو", callback_data="promo:enter"
            )
        )
    builder.row(
        InlineKeyboardButton(text=BACK_BUTTON, callback_data=back_callback),
        InlineKeyboardButton(text=CANCEL_BUTTON, callback_data="cancel_order"),
    )
    return builder.as_markup()


def payment_methods_keyboard(
    *,
    balance: int = 0,
    can_use_wallet: bool = True,
    can_use_stars: bool = True,
) -> InlineKeyboardMarkup:
    """Choose how to pay for an order."""
    builder = InlineKeyboardBuilder()
    if can_use_wallet:
        builder.row(
            InlineKeyboardButton(
                text=f"👛 کیف پول ({balance:,} تومان)",
                callback_data="pay:wallet",
            )
        )
    if can_use_stars:
        builder.row(
            InlineKeyboardButton(
                text="⭐ تلگرام استارز",
                callback_data="pay:stars",
            )
        )
    builder.row(
        InlineKeyboardButton(text="🏦 کارت به کارت", callback_data="pay:card")
    )
    builder.row(
        InlineKeyboardButton(text=CANCEL_BUTTON, callback_data="cancel_order")
    )
    return builder.as_markup()


def trial_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🎁 دریافت تست رایگان", callback_data="trial:start"
        )
    )
    return builder.as_markup()


def usage_keyboard(has_account: bool = True) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if has_account:
        builder.row(
            InlineKeyboardButton(
                text="📱 کد QR اشتراک", callback_data="usage:qr"
            )
        )
        builder.row(
            InlineKeyboardButton(text="تمدید اکانت", callback_data="usage:renew")
        )
    builder.row(
        InlineKeyboardButton(text=BACK_BUTTON, callback_data="back:main")
    )
    return builder.as_markup()


def accounts_list_keyboard(
    client_email: str | None = None,
    account_ids: List[int] | None = None,
) -> InlineKeyboardMarkup:
    """Renew buttons for the accounts shown in the list."""
    builder = InlineKeyboardBuilder()
    if client_email:
        data = f"renew_client:{client_email}"
        if callback_data_fits(data):
            builder.row(
                InlineKeyboardButton(
                    text="تمدید اکانت",
                    callback_data=data,
                )
            )
    for account_id in account_ids or []:
        data = f"renew_account:{account_id}"
        if not callback_data_fits(data):
            continue
        builder.row(
            InlineKeyboardButton(
                text=f"تمدید اکانت #{account_id}",
                callback_data=data,
            )
        )
    builder.row(
        InlineKeyboardButton(text="📊 وضعیت و کد QR", callback_data="usage:qr"),
    )
    builder.row(
        InlineKeyboardButton(text=BACK_BUTTON, callback_data="back:main"),
    )
    return builder.as_markup()


def usage_alert_keyboard(vpn_account_id: int) -> InlineKeyboardMarkup:
    """One-tap renew action attached to expiry / low-traffic alerts."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="تمدید اکانت",
            callback_data=f"renew_account:{vpn_account_id}",
        )
    )
    return builder.as_markup()


def renew_plans_keyboard(plans: List[Dict[str, Any]], vpn_account_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for plan in plans:
        data = f"renew_plan:{vpn_account_id}:{plan['id']}"
        if not callback_data_fits(data):
            # Telegram rejects the whole keyboard when one payload is too long.
            continue
        builder.row(
            InlineKeyboardButton(
                text=f"{plan['name']} - {plan['price']:,} تومان",
                callback_data=data,
            )
        )
    builder.row(
        InlineKeyboardButton(text=BACK_BUTTON, callback_data="back:accounts"),
    )
    return builder.as_markup()


def confirm_renew_keyboard(vpn_account_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="تایید تمدید", callback_data="confirm_renew_order"),
    )
    builder.row(
        InlineKeyboardButton(text=BACK_BUTTON, callback_data=f"renew_account:{vpn_account_id}"),
        InlineKeyboardButton(text=CANCEL_BUTTON, callback_data="cancel_order"),
    )
    return builder.as_markup()
