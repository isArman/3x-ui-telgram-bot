from aiogram.fsm.state import State, StatesGroup


class CustomPlanStates(StatesGroup):
    waiting_for_days = State()
    waiting_for_traffic = State()
    waiting_for_confirm = State()


class PaymentStates(StatesGroup):
    waiting_for_receipt = State()


class WalletStates(StatesGroup):
    home = State()


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
    waiting_for_card_number = State()
    waiting_for_card_holder = State()
    waiting_for_plan_id = State()
    waiting_for_plan_name = State()
    waiting_for_plan_days = State()
    waiting_for_plan_traffic = State()
    waiting_for_plan_price = State()
    waiting_for_plan_description = State()
    waiting_for_plan_edit_value = State()
    waiting_for_pricing_per_day = State()
    waiting_for_pricing_per_gb = State()
