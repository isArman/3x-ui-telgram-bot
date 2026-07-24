from aiogram.fsm.state import State, StatesGroup


class CustomPlanStates(StatesGroup):
    waiting_for_days = State()
    waiting_for_traffic = State()
    waiting_for_confirm = State()


class PaymentStates(StatesGroup):
    waiting_for_receipt = State()


class TopUpStates(StatesGroup):
    waiting_for_amount = State()
    waiting_for_confirm = State()
    waiting_for_receipt = State()


class WalletPayStates(StatesGroup):
    choosing = State()


class AdminStates(StatesGroup):
    waiting_for_subscription = State()
    waiting_for_config_text = State()
    waiting_for_panel_url = State()
    waiting_for_panel_username = State()
    waiting_for_panel_password = State()
    waiting_for_subscription_base_url = State()
    waiting_for_topup_amount = State()
