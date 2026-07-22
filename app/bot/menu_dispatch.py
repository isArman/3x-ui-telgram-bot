from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot.constants import (
    BTN_ADMIN_PANEL,
    BTN_BUY_PLAN,
    BTN_CUSTOM_PLAN,
    BTN_MY_ACCOUNTS,
    BTN_MY_ORDERS,
    CANCEL_BUTTON,
    MAIN_MENU_BUTTONS,
)


async def dispatch_main_menu(message: Message, state: FSMContext) -> bool:
    """
    Clear FSM and run the matching main-menu action.
    Returns True if the message was a menu button.
    """
    text = message.text
    if text not in MAIN_MENU_BUTTONS:
        return False

    await state.clear()

    if text == BTN_BUY_PLAN:
        from app.bot.handlers.user import show_plans

        await show_plans(message, state)
    elif text == BTN_CUSTOM_PLAN:
        from app.bot.handlers.user import custom_plan_start

        await custom_plan_start(message, state)
    elif text == BTN_MY_ORDERS:
        from app.bot.handlers.user import my_orders

        await my_orders(message, state)
    elif text == BTN_MY_ACCOUNTS:
        from app.bot.handlers.user import my_accounts

        await my_accounts(message, state)
    elif text == BTN_ADMIN_PANEL:
        from app.bot.handlers.admin import admin_menu

        await admin_menu(message, state)
    elif text == CANCEL_BUTTON:
        from app.bot.handlers.user import user_main_menu

        await message.answer("لغو شد.", reply_markup=user_main_menu(message.from_user.id))

    return True
