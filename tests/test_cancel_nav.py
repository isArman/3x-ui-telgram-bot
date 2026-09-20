"""Regression: Back/Cancel must not be swallowed by menu interrupts."""

from app.bot.constants import (
    BACK_BUTTON,
    CANCEL_BUTTON,
    FLOW_NAV_BUTTONS,
    MAIN_MENU_BUTTONS,
    PANEL_SETUP_CANCEL_TEXTS,
)
from app.bot.keyboards.admin import admin_cancel_keyboard
from app.bot.keyboards.user import confirm_topup_keyboard, wallet_pay_keyboard
from app.bot.nav import is_nav_text


def test_cancel_not_in_main_menu_buttons():
    """Menu-interrupt handlers match MAIN_MENU_BUTTONS first.

    If CANCEL were in that set, presses of ❌ لغو would hit interrupt handlers
    (e.g. admin subscription wait) and never reach real cancel logic.
    """
    assert CANCEL_BUTTON not in MAIN_MENU_BUTTONS
    assert BACK_BUTTON not in MAIN_MENU_BUTTONS


def test_cancel_and_back_are_flow_nav():
    assert CANCEL_BUTTON in FLOW_NAV_BUTTONS
    assert BACK_BUTTON in FLOW_NAV_BUTTONS


def test_main_menu_disjoint_from_flow_nav():
    assert MAIN_MENU_BUTTONS.isdisjoint(FLOW_NAV_BUTTONS)


def test_panel_setup_cancel_texts_include_nav():
    assert CANCEL_BUTTON in PANEL_SETUP_CANCEL_TEXTS
    assert BACK_BUTTON in PANEL_SETUP_CANCEL_TEXTS


def test_is_nav_text():
    assert is_nav_text(CANCEL_BUTTON)
    assert is_nav_text(BACK_BUTTON)
    assert not is_nav_text("۱۵")
    assert not is_nav_text(None)


def test_admin_cancel_keyboard_has_back_and_cancel():
    kb = admin_cancel_keyboard()
    labels = {btn.text for row in kb.keyboard for btn in row}
    assert labels == {BACK_BUTTON, CANCEL_BUTTON}


def test_wallet_pay_keyboard_has_cancel():
    kb = wallet_pay_keyboard()
    callbacks = []
    for row in kb.inline_keyboard:
        for btn in row:
            callbacks.append(btn.callback_data)
    assert "cancel_order" in callbacks
    assert "wallet_pay:yes" in callbacks


def test_confirm_topup_keyboard_has_back_and_cancel():
    kb = confirm_topup_keyboard()
    callbacks = []
    for row in kb.inline_keyboard:
        for btn in row:
            callbacks.append(btn.callback_data)
    assert "back_topup_amount" in callbacks
    assert "cancel_topup" in callbacks
    assert "confirm_topup" in callbacks
