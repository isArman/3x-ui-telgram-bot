from aiogram.fsm.state import State, StatesGroup


class CustomPlanStates(StatesGroup):
    waiting_for_days = State()
    waiting_for_traffic = State()
    waiting_for_confirm = State()


class PaymentStates(StatesGroup):
    waiting_for_receipt = State()
