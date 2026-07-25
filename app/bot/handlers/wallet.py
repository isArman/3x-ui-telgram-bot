"""User wallet menu and top-up flow."""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.bot.constants import (
    BACK_BUTTON,
    BTN_TOP_UP,
    BTN_WALLET,
    CANCEL_BUTTON,
    FLOW_NAV_BUTTONS,
    MAIN_MENU_BUTTONS,
)
from app.bot.keyboards.admin import topup_review_keyboard
from app.bot.keyboards.user import (
    confirm_topup_keyboard,
    flow_nav_keyboard,
    main_menu_keyboard,
    wallet_keyboard,
)
from app.bot.menu_dispatch import dispatch_main_menu
from app.bot.states import TopUpStates, WalletStates
from app.config.texts import get_text
from app.database.models import WalletTopUp
from app.database.session import AsyncSessionLocal
from app.services.users import get_or_create_user
from app.services.wallet import get_balance
from app.services.bot_settings import get_card_details
from app.utils.logger import logger

router = Router()


def user_main_menu(user_id: int):
    from app.config.settings import settings

    return main_menu_keyboard(is_admin=user_id in settings.ADMIN_IDS)


@router.message(
    WalletStates.home,
    F.text.in_(MAIN_MENU_BUTTONS - {CANCEL_BUTTON}),
)
@router.message(
    TopUpStates.waiting_for_amount,
    F.text.in_(MAIN_MENU_BUTTONS),
)
@router.message(
    TopUpStates.waiting_for_confirm,
    F.text.in_(MAIN_MENU_BUTTONS),
)
@router.message(
    TopUpStates.waiting_for_receipt,
    F.text.in_(MAIN_MENU_BUTTONS),
)
async def topup_menu_interrupt(message: Message, state: FSMContext):
    await dispatch_main_menu(message, state)


@router.message(F.text == BTN_WALLET)
async def show_wallet(message: Message, state: FSMContext):
    """Show wallet balance and top-up option."""
    await state.clear()
    async with AsyncSessionLocal() as session:
        await get_or_create_user(session, message.from_user)
        await session.commit()
        balance = await get_balance(session, message.from_user.id)

    await state.set_state(WalletStates.home)
    await message.answer(
        get_text("wallet_home", balance=balance),
        reply_markup=wallet_keyboard(),
    )


@router.message(WalletStates.home, F.text.in_({BACK_BUTTON, CANCEL_BUTTON}))
async def wallet_home_back(message: Message, state: FSMContext):
    """Return to main menu from wallet home (بازگشت / لغو)."""
    await state.clear()
    await message.answer(
        get_text("start"),
        reply_markup=user_main_menu(message.from_user.id),
    )


@router.message(F.text == BTN_TOP_UP)
async def topup_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(TopUpStates.waiting_for_amount)
    await message.answer(
        get_text("wallet_topup_ask_amount"),
        reply_markup=flow_nav_keyboard(),
    )


@router.message(TopUpStates.waiting_for_amount, F.text == BACK_BUTTON)
async def topup_amount_back(message: Message, state: FSMContext):
    await show_wallet(message, state)


@router.message(TopUpStates.waiting_for_amount, F.text == CANCEL_BUTTON)
async def topup_amount_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        get_text("operation_cancelled"),
        reply_markup=user_main_menu(message.from_user.id),
    )


@router.message(TopUpStates.waiting_for_amount)
async def topup_amount(message: Message, state: FSMContext):
    try:
        amount = int(message.text.replace(",", "").replace("،", "").strip())
        if amount < 1:
            await message.answer(get_text("error_invalid_number"))
            return
    except (TypeError, ValueError):
        await message.answer(get_text("error_invalid_number"))
        return

    await state.update_data(topup_amount=amount)
    await state.set_state(TopUpStates.waiting_for_confirm)
    # Keep reply nav so BACK/CANCEL still work alongside inline confirm.
    await message.answer(
        get_text("wallet_topup_confirm", amount=amount),
        reply_markup=confirm_topup_keyboard(),
    )


@router.message(TopUpStates.waiting_for_confirm, F.text == BACK_BUTTON)
async def topup_confirm_back(message: Message, state: FSMContext):
    await state.set_state(TopUpStates.waiting_for_amount)
    await message.answer(
        get_text("wallet_topup_ask_amount"),
        reply_markup=flow_nav_keyboard(),
    )


@router.message(TopUpStates.waiting_for_confirm, F.text == CANCEL_BUTTON)
async def topup_confirm_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        get_text("operation_cancelled"),
        reply_markup=user_main_menu(message.from_user.id),
    )


@router.callback_query(F.data == "cancel_topup")
async def cancel_topup(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await callback.bot.send_message(
        chat_id=callback.from_user.id,
        text=get_text("operation_cancelled"),
        reply_markup=user_main_menu(callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(F.data == "confirm_topup")
async def confirm_topup(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    amount = data.get("topup_amount")
    if not amount or int(amount) < 1:
        await callback.answer("مبلغ نامعتبر است.", show_alert=True)
        await state.clear()
        return

    amount = int(amount)

    async with AsyncSessionLocal() as session:
        await get_or_create_user(session, callback.from_user)
        topup = WalletTopUp(
            user_id=callback.from_user.id,
            requested_amount=amount,
            status="pending",
        )
        session.add(topup)
        await session.commit()
        await session.refresh(topup)
        card_number, card_holder = await get_card_details(session)

    if not card_number or not card_holder:
        await callback.message.delete()
        await callback.bot.send_message(
            chat_id=callback.from_user.id,
            text=(
                "❌ اطلاعات کارت بانکی هنوز توسط ادمین تنظیم نشده است.\n"
                "لطفاً بعداً دوباره تلاش کنید."
            ),
            reply_markup=user_main_menu(callback.from_user.id),
        )
        await state.clear()
        await callback.answer()
        return

    await callback.message.delete()
    await callback.bot.send_message(
        chat_id=callback.from_user.id,
        text=get_text(
            "wallet_topup_created",
            topup_id=topup.id,
            amount=amount,
            card_number=card_number,
            card_holder=card_holder,
        ),
        reply_markup=flow_nav_keyboard(),
    )
    await state.update_data(topup_id=topup.id)
    await state.set_state(TopUpStates.waiting_for_receipt)
    logger.info("Top-up %s created by user %s for %s", topup.id, callback.from_user.id, amount)
    await callback.answer()


@router.message(TopUpStates.waiting_for_receipt, F.text == BACK_BUTTON)
@router.message(TopUpStates.waiting_for_receipt, F.text == CANCEL_BUTTON)
async def topup_receipt_back(message: Message, state: FSMContext):
    data = await state.get_data()
    topup_id = data.get("topup_id")
    await state.clear()
    note = ""
    if topup_id:
        note = (
            f"\n\n🔢 درخواست #{topup_id} همچنان ثبت است. "
            "برای ارسال رسید دوباره «کیف پول من» را باز کنید."
        )
    await message.answer(
        get_text("start") + note,
        reply_markup=user_main_menu(message.from_user.id),
    )


@router.message(TopUpStates.waiting_for_receipt, F.photo)
async def topup_receive_receipt(message: Message, state: FSMContext):
    data = await state.get_data()
    topup_id = data.get("topup_id")
    if not topup_id:
        await message.answer(get_text("error_general"))
        await state.clear()
        return

    photo = message.photo[-1]

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(WalletTopUp).where(WalletTopUp.id == topup_id)
        )
        topup = result.scalar_one_or_none()

        if not topup or topup.user_id != message.from_user.id:
            await message.answer(get_text("error_general"))
            await state.clear()
            return

        if topup.status != "pending":
            await message.answer(
                "این درخواست قابل ارسال رسید نیست.",
                reply_markup=user_main_menu(message.from_user.id),
            )
            await state.clear()
            return

        if topup.receipt_file_id:
            await message.answer(
                "رسید قبلی شما هنوز در انتظار بررسی ادمین است.",
                reply_markup=user_main_menu(message.from_user.id),
            )
            await state.clear()
            return

        topup.receipt_file_id = photo.file_id
        await session.commit()

        await message.answer(
            get_text("wallet_topup_receipt_received"),
            reply_markup=user_main_menu(message.from_user.id),
        )

        admin_text = get_text(
            "admin_new_topup",
            user_id=message.from_user.id,
            username=message.from_user.username or "بدون نام کاربری",
            topup_id=topup.id,
            amount=topup.requested_amount,
        )

        for admin_id in settings.ADMIN_IDS:
            try:
                await message.bot.send_photo(
                    chat_id=admin_id,
                    photo=photo.file_id,
                    caption=admin_text,
                    reply_markup=topup_review_keyboard(
                        topup.id, topup.requested_amount
                    ),
                )
            except Exception as exc:
                logger.warning(
                    "Failed to notify admin %s about topup %s: %s",
                    admin_id,
                    topup.id,
                    exc,
                )

    await state.clear()


_MENU_BUTTONS = MAIN_MENU_BUTTONS | FLOW_NAV_BUTTONS | {BTN_TOP_UP}


@router.message(TopUpStates.waiting_for_receipt, ~F.text.in_(_MENU_BUTTONS))
@router.message(TopUpStates.waiting_for_amount, ~F.text.in_(_MENU_BUTTONS))
async def topup_invalid_input(message: Message, state: FSMContext):
    current = await state.get_state()
    if current == TopUpStates.waiting_for_receipt.state:
        await message.answer(
            "لطفاً تصویر رسید پرداخت را ارسال کنید.\n"
            f"یا از «{BACK_BUTTON}» برای برگشت به منو استفاده کنید."
        )
    else:
        await message.answer(get_text("error_invalid_number"))
