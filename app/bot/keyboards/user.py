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
        InlineKeyboardButton(text="✅ تایید", callback_data="confirm_topup"),
    )
    builder.row(
        InlineKeyboardButton(text=CANCEL_BUTTON, callback_data="cancel_topup"),
    )
    return builder.as_markup()


def wallet_pay_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ بله، استفاده از کیف پول",
            callback_data="wallet_pay:yes",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="💳 خیر، فقط کارت",
            callback_data="wallet_pay:no",
        ),
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
        builder.row(
            InlineKeyboardButton(
                text=f"{plan['name']} - {plan['price']:,} تومان",
                callback_data=f"plan:{plan['id']}",
            )
        )

    builder.row(
        InlineKeyboardButton(text=BACK_BUTTON, callback_data="back:main"),
    )

    return builder.as_markup()


def confirm_order_keyboard(back_callback: str = "back:plans") -> InlineKeyboardMarkup:
    """Order confirmation keyboard"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ تایید", callback_data="confirm_order"),
    )
    builder.row(
        InlineKeyboardButton(text=BACK_BUTTON, callback_data=back_callback),
        InlineKeyboardButton(text=CANCEL_BUTTON, callback_data="cancel_order"),
    )
    return builder.as_markup()


def accounts_list_keyboard(accounts) -> InlineKeyboardMarkup:
    """Renew buttons for each VPN account."""
    builder = InlineKeyboardBuilder()
    for account in accounts:
        builder.row(
            InlineKeyboardButton(
                text=f"🔄 تمدید سفارش #{account.order_id}",
                callback_data=f"renew_account:{account.id}",
            )
        )
    builder.row(
        InlineKeyboardButton(text=BACK_BUTTON, callback_data="back:main"),
    )
    return builder.as_markup()


def renew_plans_keyboard(plans: List[Dict[str, Any]], vpn_account_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for plan in plans:
        builder.row(
            InlineKeyboardButton(
                text=f"{plan['name']} - {plan['price']:,} تومان",
                callback_data=f"renew_plan:{vpn_account_id}:{plan['id']}",
            )
        )
    builder.row(
        InlineKeyboardButton(text=BACK_BUTTON, callback_data="back:accounts"),
    )
    return builder.as_markup()


def confirm_renew_keyboard(vpn_account_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ تایید تمدید", callback_data="confirm_renew_order"),
    )
    builder.row(
        InlineKeyboardButton(text=BACK_BUTTON, callback_data=f"renew_account:{vpn_account_id}"),
        InlineKeyboardButton(text=CANCEL_BUTTON, callback_data="cancel_order"),
    )
    return builder.as_markup()
