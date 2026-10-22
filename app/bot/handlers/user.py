from datetime import datetime, timezone

from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    KeyboardButton,
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    WebAppInfo,
)
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.texts import get_text
from app.config.settings import settings
from app.database.models import User, Order, Payment, VPNAccount
from app.database.session import AsyncSessionLocal
from app.bot.constants import (
    BACK_BUTTON,
    BTN_ADMIN_PANEL,
    BTN_BUY_PLAN,
    BTN_CUSTOM_PLAN,
    BTN_MY_ACCOUNTS,
    BTN_MY_ORDERS,
    BTN_REFERRAL,
    CANCEL_BUTTON,
    FLOW_NAV_BUTTONS,
    MAIN_MENU_BUTTONS,
    ORDER_STATUS_LABELS,
    button_texts,
)
from app.bot.states import (
    CheckoutStates,
    CustomPlanStates,
    PaymentStates,
    WalletPayStates,
)
from app.bot.keyboards.user import (
    main_menu_keyboard,
    plans_keyboard,
    confirm_order_keyboard,
    confirm_renew_keyboard,
    accounts_list_keyboard,
    renew_plans_keyboard,
    flow_nav_keyboard,
    wallet_pay_keyboard,
    payment_methods_keyboard,
    trial_keyboard,
    usage_keyboard,
)
from app.services.users import get_or_create_user
from app.services.wallet import (
    InsufficientBalanceError,
    debit_balance,
    get_balance,
)
from app.services.order_fulfillment import fulfill_paid_order
from app.services.panel_settings import (
    get_panel_api_token,
    get_panel_password,
    get_panel_settings,
    has_panel_credentials,
    panel_auth_mode,
    set_panel_api_token,
    set_panel_password,
    xui_client_for_panel,
)
from app.services.traffic_usage import format_gb, client_record, used_bytes
from app.xui.client import build_subscription_url
from app.services.referral import (
    apply_purchase_discount,
    ensure_referral_code,
    format_order_price_lines,
    format_price_block,
    is_discount_eligible,
    preview_discounted_price,
    referral_link,
    try_bind_referrer,
)
from app.services.bot_settings import get_card_details, is_maintenance
from app.services.plans_catalog import get_plan, get_pricing, list_active_plans
from app.services.promocodes import (
    PromoApplication,
    get_promo,
    normalize_code,
    reserve_promo,
    validate_promo,
)
from app.services.qr import subscription_qr_png
from app.services.telegram_payments import (
    build_stars_invoice,
    invoice_payload,
    order_id_from_payload,
    send_stars_invoice,
)
from app.services.trial import (
    create_trial_order,
    get_trial_settings,
    mark_trial_used,
    trial_available,
)

router = Router()

_bot_username_cache: str | None = None


async def get_bot_username(bot) -> str:
    global _bot_username_cache
    if _bot_username_cache:
        return _bot_username_cache
    me = await bot.get_me()
    _bot_username_cache = me.username or ""
    return _bot_username_cache


async def notify_referral_cashback(bot, cashback: tuple[int, int] | None) -> None:
    if not cashback:
        return
    referrer_id, amount = cashback
    try:
        await bot.send_message(
            chat_id=referrer_id,
            text=get_text("referral_cashback_notify", amount=amount),
        )
    except Exception as exc:
        from app.utils.logger import logger

        logger.warning(
            "Failed to notify referrer %s about cashback: %s", referrer_id, exc
        )


def user_main_menu(user_id: int):
    return main_menu_keyboard(is_admin=user_id in settings.ADMIN_IDS)


def build_plans_text(plans: list, *, referral_hint: str = "") -> str:
    text = get_text("plans_list")
    if referral_hint:
        text += referral_hint
    if not plans:
        return text + "هنوز پلنی ثبت نشده است.\n"
    for plan in plans:
        text += get_text("plan_details", **plan) + "\n"
    return text


async def referral_plans_hint_for(session: AsyncSession, tg_user) -> str:
    user, _ = await get_or_create_user(session, tg_user)
    if await is_discount_eligible(session, user):
        return get_text("referral_plans_hint")
    return ""


async def price_block_for_tg_user(
    session: AsyncSession,
    tg_user,
    list_price: int,
    promo_discount: int = 0,
) -> str:
    user, _ = await get_or_create_user(session, tg_user)
    payable = max(int(list_price) - int(promo_discount or 0), 0)
    eligible = await is_discount_eligible(session, user)
    if eligible:
        payable, _, _ = apply_purchase_discount(payable)
    return format_price_block(
        int(list_price),
        payable=payable,
        applied=bool(promo_discount) or eligible,
    )


async def send_plans_menu(bot, chat_id: int, tg_user=None) -> None:
    async with AsyncSessionLocal() as session:
        plans = await list_active_plans(session)
        hint = ""
        if tg_user is not None:
            hint = await referral_plans_hint_for(session, tg_user)
    await bot.send_message(
        chat_id=chat_id,
        text=build_plans_text(plans, referral_hint=hint),
        reply_markup=plans_keyboard(plans),
    )


async def send_main_menu_message(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        get_text("start"),
        reply_markup=user_main_menu(message.from_user.id),
    )


def calculate_custom_price(days: int, traffic_gb: int, pricing: dict) -> int:
    """Calculate price for custom plan"""
    return (days * pricing["per_day"]) + (traffic_gb * pricing["per_gb"])


async def resolve_order_pricing(session: AsyncSession, data: dict):
    """
    Resolve authoritative plan pricing from DB.

    Ready-made plans: days/traffic/price/devices come from shop_plans via plan_id.
    Custom plans: recompute price from days/traffic + pricing formula.

    Returns (plan_id, days, traffic_gb, price, devices) or None if invalid.
    """
    plan_id = data.get("plan_id")
    if plan_id:
        plan = await get_plan(session, plan_id, active_only=True)
        if not plan:
            return None
        return (
            plan_id,
            int(plan["days"]),
            int(plan["traffic"]),
            int(plan["price"]),
            int(plan.get("devices") or 1),
        )

    try:
        days = int(data["days"])
        traffic = int(data["traffic"])
    except (KeyError, TypeError, ValueError):
        return None

    if not 1 <= days <= 365 or not 1 <= traffic <= 500:
        return None

    pricing = await get_pricing(session)
    return None, days, traffic, calculate_custom_price(days, traffic, pricing), 1


async def get_or_create_user_from_message(
    session: AsyncSession, message: Message
) -> tuple[User, bool]:
    return await get_or_create_user(session, message.from_user)


async def prompt_payment_methods(
    bot, user_id: int, order: Order, state: FSMContext
) -> None:
    """Ask the customer how they want to pay for an order."""
    async with AsyncSessionLocal() as session:
        balance = await get_balance(session, user_id)

    await bot.send_message(
        chat_id=user_id,
        text=get_text(
            "payment_methods",
            order_id=order.id,
            price_lines=format_order_price_lines(order),
            balance=balance,
        ),
        reply_markup=payment_methods_keyboard(
            balance=balance,
            can_use_wallet=balance > 0,
        ),
    )
    await state.update_data(order_id=order.id)
    await state.set_state(WalletPayStates.choosing)


async def prompt_wallet_payment(bot, user_id: int, order: Order, state: FSMContext) -> None:
    """Backwards-compatible alias for the payment-method prompt."""
    await prompt_payment_methods(bot, user_id, order, state)


async def deliver_free_order(
    bot, user_id: int, order: Order, state: FSMContext
) -> None:
    """Deliver a zero-priced order immediately (no payment is involved)."""
    from app.utils.logger import logger

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Order).where(Order.id == order.id))
        fresh = result.scalar_one_or_none()
        if not fresh or fresh.user_id != user_id:
            await state.clear()
            return

        fresh.status = "paid"
        payment = Payment(
            order_id=fresh.id,
            user_id=user_id,
            receipt_file_id="free",
            status="pending",
        )
        session.add(payment)
        await session.commit()
        await session.refresh(payment)
        await session.refresh(fresh)

        try:
            await fulfill_full_wallet_order(
                bot,
                session,
                payment,
                fresh,
                paid_note=(
                    "سفارش شما ثبت شد.\n"
                    "اکانت شما به‌زودی توسط ادمین ارسال می‌شود."
                ),
            )
        except Exception as exc:
            logger.error("Failed to deliver free order %s: %s", fresh.id, exc)
            await notify_admins_wallet_manual(bot, payment, fresh)

    await state.clear()


async def send_card_payment_instructions(
    bot,
    user_id: int,
    order: Order,
    state: FSMContext,
    *,
    wallet_amount: int = 0,
) -> None:
    async with AsyncSessionLocal() as session:
        card_number, card_holder = await get_card_details(session)

    if not card_number or not card_holder:
        if int(order.wallet_debit or 0) > 0 or wallet_amount > 0:
            await refund_wallet_on_cancel(order.id, user_id)
        await bot.send_message(
            chat_id=user_id,
            text=(
                "اطلاعات کارت بانکی هنوز تنظیم نشده است.\n"
                "کمی بعد دوباره تلاش کنید."
            ),
            reply_markup=user_main_menu(user_id),
        )
        await state.clear()
        return

    difference = order.price - wallet_amount
    price_lines = format_order_price_lines(order)
    if wallet_amount > 0:
        text = get_text(
            "order_created_partial",
            order_id=order.id,
            price_lines=price_lines,
            wallet_amount=wallet_amount,
            difference=difference,
            card_number=card_number,
            card_holder=card_holder,
        )
    else:
        text = get_text(
            "order_created",
            order_id=order.id,
            price_lines=price_lines,
            card_number=card_number,
            card_holder=card_holder,
        )

    await bot.send_message(
        chat_id=user_id,
        text=text,
        reply_markup=flow_nav_keyboard(),
    )
    await state.update_data(order_id=order.id)
    await state.set_state(PaymentStates.waiting_for_receipt)


async def notify_admins_wallet_manual(bot, payment: Payment, order: Order) -> None:
    """Notify admins that a wallet-paid order needs a manual subscription link."""
    from app.bot.keyboards.admin import payment_review_keyboard

    wallet_debit = int(order.wallet_debit or 0)
    text = (
        "سفارش با کیف پول پرداخت شد و به لینک دستی نیاز دارد.\n\n"
        f"کاربر: {order.user_id}\n"
        f"سفارش: #{order.id}\n"
        f"{order.days} روز | {order.traffic_gb} گیگابایت\n"
        f"مبلغ: {order.price:,} تومان\n"
        f"از کیف پول: {wallet_debit:,} تومان\n\n"
        "تایید کنید و لینک را بفرستید."
    )
    for admin_id in settings.ADMIN_IDS:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=text,
                reply_markup=payment_review_keyboard(payment.id),
            )
        except Exception as exc:
            from app.utils.logger import logger

            logger.warning(
                "Failed to notify admin %s about wallet order %s: %s",
                admin_id,
                order.id,
                exc,
            )


async def fulfill_full_wallet_order(
    bot,
    session,
    payment: Payment,
    order: Order,
    *,
    paid_note: str | None = None,
) -> None:
    """Fulfill an order fully paid from wallet (no receipt)."""
    from app.bot.handlers.admin import send_config_to_user, send_renewal_to_user
    from app.utils.logger import logger

    plan = None
    if order.plan_id:
        plan = await get_plan(session, order.plan_id)
    plan_name = plan["name"] if plan else None

    fulfill = await fulfill_paid_order(session, payment, order, None, plan_name)
    await notify_referral_cashback(bot, fulfill.referral_cashback)

    if fulfill.kind in ("renewal_auto", "renewal_db") and fulfill.vpn_account:
        await send_renewal_to_user(bot, order.user_id, fulfill.vpn_account, order)
        logger.info(
            "Wallet-fulfilled renewal order %s kind=%s", order.id, fulfill.kind
        )
        return

    if fulfill.kind in ("new_auto", "new_inventory") and fulfill.config_text:
        await send_config_to_user(bot, order.user_id, fulfill.config_text)
        logger.info("Wallet-fulfilled order %s kind=%s", order.id, fulfill.kind)
        return

    # needs_manual — leave payment pending for admin
    await notify_admins_wallet_manual(bot, payment, order)
    await bot.send_message(
        chat_id=order.user_id,
        text=paid_note
        or (
            "پرداخت از کیف پول ثبت شد.\n"
            "اکانت شما به‌زودی توسط ادمین ارسال می‌شود."
        ),
    )
    logger.info("Wallet order %s awaiting manual config", order.id)


async def refund_wallet_on_cancel(order_id: int | None, user_id: int) -> int | None:
    """
    Abandon an unpaid order: refund wallet_debit if any, mark pending as rejected.
    Returns new balance when a refund happened, else None.
    """
    if not order_id:
        return None

    from app.bot.handlers.admin import refund_order_wallet_debit

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Order).where(Order.id == order_id))
        order = result.scalar_one_or_none()
        if not order or order.user_id != user_id:
            return None
        if order.status not in ("pending", "paid"):
            return None
        # Don't touch orders already under admin review
        existing = await session.execute(
            select(Payment).where(
                Payment.order_id == order_id,
                Payment.status.in_(("pending", "approved")),
            )
        )
        if existing.scalar_one_or_none():
            return None

        new_balance = None
        if int(order.wallet_debit or 0) > 0:
            new_balance = await refund_order_wallet_debit(session, order)
        if order.status == "pending":
            order.status = "rejected"
        await session.commit()
        return new_balance


async def abandon_current_order_if_any(state: FSMContext, user_id: int) -> int | None:
    data = await state.get_data()
    return await refund_wallet_on_cancel(data.get("order_id"), user_id)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    """Start command handler — opens the Mini App."""
    await abandon_current_order_if_any(state, message.from_user.id)
    await state.clear()

    payload = None
    if message.text:
        parts = message.text.split(maxsplit=1)
        if len(parts) > 1:
            payload = parts[1].strip() or None

    bound = False
    eligible = False
    async with AsyncSessionLocal() as session:
        user, created = await get_or_create_user_from_message(session, message)
        if created and payload:
            bound = await try_bind_referrer(session, user, payload, is_new_user=True)
        await session.commit()
        eligible = await is_discount_eligible(session, user)

    text = get_text("start")
    if bound:
        text += get_text("referral_bound_notice")
    elif eligible:
        text += get_text("referral_eligible_notice")

    await message.answer(text, reply_markup=mini_app_keyboard())
    if payload:
        await message.answer(
            "برای استفاده از کد معرف، لینک دعوت را در Mini App باز کنید.",
            reply_markup=mini_app_keyboard(),
        )


def mini_app_keyboard() -> ReplyKeyboardMarkup:
    """The reply keyboard that opens the Mini App."""
    from aiogram.types import ReplyKeyboardRemove

    url = settings.MINIAPP_URL
    if not url:
        return ReplyKeyboardRemove()
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🛒 فروشگاه", web_app=WebAppInfo(url=url))]],
        resize_keyboard=True,
    )


@router.message(F.text.in_(button_texts(BTN_REFERRAL)))
async def show_referral(message: Message, state: FSMContext):
    """Show user's referral code and invite link."""
    await abandon_current_order_if_any(state, message.from_user.id)
    await state.clear()
    async with AsyncSessionLocal() as session:
        user, _ = await get_or_create_user(session, message.from_user)
        code = await ensure_referral_code(session, user)
        await session.commit()

        if await is_discount_eligible(session, user):
            status_line = get_text("referral_status_active")
        elif user.referred_by_user_id:
            status_line = get_text("referral_status_used")
        else:
            status_line = get_text("referral_status_none")

    username = await get_bot_username(message.bot)
    if not username:
        await message.answer(
            get_text("error_general"),
            reply_markup=user_main_menu(message.from_user.id),
        )
        return

    await message.answer(
        get_text(
            "referral_invite",
            code=code,
            link=referral_link(username, code),
            status_line=status_line,
        ),
        reply_markup=user_main_menu(message.from_user.id),
    )


@router.message(F.text.in_(button_texts(BTN_BUY_PLAN)))
async def show_plans(message: Message, state: FSMContext):
    """Show available plans"""
    await abandon_current_order_if_any(state, message.from_user.id)
    await state.clear()
    async with AsyncSessionLocal() as session:
        plans = await list_active_plans(session)
        hint = await referral_plans_hint_for(session, message.from_user)
    await message.answer(
        build_plans_text(plans, referral_hint=hint),
        reply_markup=plans_keyboard(plans),
    )


async def _render_checkout(
    message: Message,
    state: FSMContext,
    *,
    custom: bool,
    back_callback: str = "back:plans",
    edit: bool = False,
) -> None:
    """
    Show the order confirmation screen.

    Any promo code stored in the FSM is re-validated here, so the price shown
    always matches what the order will actually cost.
    """
    data = await state.get_data()
    days = int(data.get("days") or 0)
    traffic = int(data.get("traffic") or 0)
    base_price = int(data.get("price") or 0)
    promo_code = data.get("promo_code")

    bonus_days = bonus_gb = promo_discount = 0
    if promo_code:
        async with AsyncSessionLocal() as session:
            application, _ = await validate_promo(
                session,
                promo_code,
                user_id=message.from_user.id,
                price=base_price,
            )
        if application:
            bonus_days = application.bonus_days
            bonus_gb = application.bonus_gb
            promo_discount = application.discount

    async with AsyncSessionLocal() as session:
        price_block = await price_block_for_tg_user(
            session, message.from_user, base_price, promo_discount
        )

    extra = ""
    if promo_discount:
        extra += "\n" + get_text("promo_discount", amount=promo_discount)
    if bonus_days:
        extra += "\n" + get_text("promo_days_added", days=bonus_days)
    if bonus_gb:
        extra += "\n" + get_text("promo_gb_added", traffic=bonus_gb)

    if custom:
        text = get_text(
            "custom_plan_confirm",
            days=days + bonus_days,
            traffic=traffic + bonus_gb,
            price_block=price_block,
        )
    else:
        text = get_text(
            "plan_confirm",
            name=data.get("plan_name") or "",
            days=days + bonus_days,
            traffic=traffic + bonus_gb,
            devices=data.get("plan_devices") or 1,
            price_block=price_block,
            description=(data.get("plan_description") or ""),
        )

    reply_markup = confirm_order_keyboard(
        back_callback, has_promo=bool(promo_code)
    )
    if edit:
        await message.edit_text(text + extra, reply_markup=reply_markup)
    else:
        await message.answer(text + extra, reply_markup=reply_markup)


@router.callback_query(F.data == "promo:enter")
async def promo_enter(callback: CallbackQuery, state: FSMContext):
    """Ask the customer for a promo code."""
    await state.set_state(CheckoutStates.waiting_for_promo)
    await callback.message.answer(
        get_text("promo_enter"),
        reply_markup=flow_nav_keyboard(),
    )
    await callback.answer()


@router.message(CheckoutStates.waiting_for_promo)
async def promo_receive(message: Message, state: FSMContext):
    """Apply a promo code to the order being composed."""
    text = (message.text or "").strip()
    if text == CANCEL_BUTTON:
        await state.clear()
        await message.answer(
            get_text("start"), reply_markup=user_main_menu(message.from_user.id)
        )
        return

    data = await state.get_data()
    base_price = int(data.get("price") or 0)
    async with AsyncSessionLocal() as session:
        application, error = await validate_promo(
            session,
            text,
            user_id=message.from_user.id,
            price=base_price,
        )
        if not application:
            await message.answer(get_text("promo_invalid", reason=error))
            return

    await state.update_data(promo_code=application.code)
    await state.set_state(None)
    await message.answer(get_text("promo_applied", code=application.code))
    await _render_checkout(message, state, custom=not data.get("plan_id"))


@router.callback_query(F.data == "trial:start")
async def trial_start(callback: CallbackQuery, state: FSMContext):
    """Give the customer their one free trial."""
    from app.utils.logger import logger

    async with AsyncSessionLocal() as session:
        can_take, reason = await trial_available(session, callback.from_user.id)
        if not can_take:
            await callback.answer(reason, show_alert=True)
            return

        user, _ = await get_or_create_user(session, callback.from_user)
        trial_settings = await get_trial_settings(session)
        order = await create_trial_order(session, user, trial_settings)
        await mark_trial_used(session, user)
        await session.commit()
        await session.refresh(order)

    await callback.answer()
    await callback.message.answer(get_text("trial_claimed"))

    try:
        async with AsyncSessionLocal() as session:
            await fulfill_trial_order(callback.bot, session, order)
    except Exception as exc:
        logger.error("Failed to deliver trial order %s: %s", order.id, exc)
        await callback.message.answer(
            "ثبت تست انجام شد اما فعال‌سازی خودکار ناموفق بود. ادمین به‌زودی اکانت را می‌فرستد."
        )


async def fulfill_trial_order(bot, session: AsyncSession, order: Order) -> None:
    """Deliver a trial the same way a paid order is delivered."""
    from app.bot.handlers.admin import send_config_to_user, send_renewal_to_user
    from app.services.order_fulfillment import fulfill_paid_order

    plan = await get_plan(session, order.plan_id) if order.plan_id else None
    plan_name = plan["name"] if plan else None

    payment = Payment(
        order_id=order.id,
        user_id=order.user_id,
        receipt_file_id="trial",
        status="pending",
        method="trial",
    )
    session.add(payment)
    await session.commit()
    await session.refresh(payment)

    fulfill = await fulfill_paid_order(session, payment, order, None, plan_name)
    if fulfill.kind in ("renewal_auto", "renewal_db") and fulfill.vpn_account:
        await send_renewal_to_user(bot, order.user_id, fulfill.vpn_account, order)
        return
    if fulfill.kind in ("new_auto", "new_inventory") and fulfill.config_text:
        await send_config_to_user(bot, order.user_id, fulfill.config_text)
        return
    await notify_admins_trial_manual(bot, order, payment)


async def notify_admins_trial_manual(bot, order: Order, payment: Payment) -> None:
    """Tell the admins that a trial needs a manual subscription link."""
    await notify_admins_wallet_manual(bot, payment, order)


@router.callback_query(F.data.startswith("plan:"))
async def select_plan(callback: CallbackQuery, state: FSMContext):
    """Handle plan selection"""
    plan_id = callback.data.split(":")[1]
    async with AsyncSessionLocal() as session:
        if await is_maintenance(session):
            await callback.answer(get_text("maintenance_notice"), show_alert=True)
            return
        plan = await get_plan(session, plan_id, active_only=True)
        if not plan:
            await callback.answer("پلن پیدا نشد.", show_alert=True)
            return

    await abandon_current_order_if_any(state, callback.from_user.id)
    await state.clear()
    await state.update_data(
        plan_id=plan_id,
        days=plan["days"],
        traffic=plan["traffic"],
        price=plan["price"],
        plan_name=plan["name"],
        plan_devices=plan.get("devices") or 1,
        plan_description=(plan.get("description") or "").strip(),
    )

    await _render_checkout(
        callback.message,
        state,
        custom=False,
        back_callback="back:plans",
        edit=True,
    )
    await callback.answer()


@router.message(F.text.in_(button_texts(BTN_CUSTOM_PLAN)))
async def custom_plan_start(message: Message, state: FSMContext):
    """Start custom plan flow"""
    # Clear any leftover plan_id from a previously selected ready-made plan
    await abandon_current_order_if_any(state, message.from_user.id)
    await state.clear()
    await state.set_state(CustomPlanStates.waiting_for_days)
    await message.answer(
        get_text("custom_plan_start"),
        reply_markup=flow_nav_keyboard(),
    )


@router.message(CustomPlanStates.waiting_for_days, F.text == BACK_BUTTON)
async def custom_plan_days_back(message: Message, state: FSMContext):
    await send_main_menu_message(message, state)


@router.message(CustomPlanStates.waiting_for_days, F.text == CANCEL_BUTTON)
async def custom_plan_days_cancel(message: Message, state: FSMContext):
    await send_main_menu_message(message, state)


@router.message(CustomPlanStates.waiting_for_days)
async def custom_plan_days(message: Message, state: FSMContext):
    """Handle custom plan days input"""
    try:
        days = int((message.text or "").strip())
        if not 1 <= days <= 365:
            await message.answer(get_text("error_out_of_range"))
            return

        await state.update_data(days=days)
        await state.set_state(CustomPlanStates.waiting_for_traffic)
        await message.answer(
            get_text("custom_plan_traffic"),
            reply_markup=flow_nav_keyboard(),
        )
    except (TypeError, ValueError):
        await message.answer(get_text("error_invalid_number"))


@router.message(CustomPlanStates.waiting_for_traffic, F.text == BACK_BUTTON)
async def custom_plan_traffic_back(message: Message, state: FSMContext):
    await state.set_state(CustomPlanStates.waiting_for_days)
    await message.answer(
        get_text("custom_plan_start"),
        reply_markup=flow_nav_keyboard(),
    )


@router.message(CustomPlanStates.waiting_for_traffic, F.text == CANCEL_BUTTON)
async def custom_plan_traffic_cancel(message: Message, state: FSMContext):
    await send_main_menu_message(message, state)


@router.message(CustomPlanStates.waiting_for_traffic)
async def custom_plan_traffic(message: Message, state: FSMContext):
    """Handle custom plan traffic input"""
    try:
        traffic = int((message.text or "").strip())
        if not 1 <= traffic <= 500:
            await message.answer(get_text("error_out_of_range"))
            return

        data = await state.get_data()
        days = data.get("days")
        if not days:
            await message.answer(get_text("error_general"))
            await state.set_state(CustomPlanStates.waiting_for_days)
            return
        async with AsyncSessionLocal() as session:
            pricing = await get_pricing(session)
            price = calculate_custom_price(days, traffic, pricing)
            price_block = await price_block_for_tg_user(
                session, message.from_user, price
            )

        await state.update_data(traffic=traffic, price=price, plan_id=None)

        await _render_checkout(message, state, custom=True, back_callback="back:custom_traffic")
        await state.set_state(CustomPlanStates.waiting_for_confirm)
    except (TypeError, ValueError):
        await message.answer(get_text("error_invalid_number"))


@router.message(CustomPlanStates.waiting_for_confirm, F.text == BACK_BUTTON)
async def custom_plan_confirm_back(message: Message, state: FSMContext):
    await state.set_state(CustomPlanStates.waiting_for_traffic)
    await message.answer(
        get_text("custom_plan_traffic"),
        reply_markup=flow_nav_keyboard(),
    )


@router.message(CustomPlanStates.waiting_for_confirm, F.text == CANCEL_BUTTON)
async def custom_plan_confirm_cancel(message: Message, state: FSMContext):
    await send_main_menu_message(message, state)


@router.callback_query(F.data == "confirm_order")
async def confirm_order(callback: CallbackQuery, state: FSMContext):
    """Confirm and create order"""
    from app.utils.logger import logger
    from app.utils.rate_limiter import rate_limiter
    
    # Rate limiting - 1 order per 60 seconds
    can_proceed, remaining = rate_limiter.check_limit(
        callback.from_user.id, 
        "create_order", 
        seconds=60
    )
    
    if not can_proceed:
        await callback.answer(
            f"{remaining} ثانیه صبر کنید و بعد سفارش جدید ثبت کنید.",
            show_alert=True
        )
        logger.warning(f"User {callback.from_user.id} hit rate limit for order creation")
        return
    
    data = await state.get_data()
    async with AsyncSessionLocal() as session:
        if await is_maintenance(session):
            await callback.answer(get_text("maintenance_notice"), show_alert=True)
            return
        resolved = await resolve_order_pricing(session, data)

    if not resolved:
        await callback.answer(
            "اطلاعات سفارش نامعتبر است. دوباره پلن را انتخاب کنید.",
            show_alert=True,
        )
        await state.clear()
        return

    plan_id, days, traffic_gb, price, devices = resolved

    try:
        async with AsyncSessionLocal() as session:
            user, _ = await get_or_create_user(session, callback.from_user)

            # A promo code is re-validated here: the state is only a hint, the
            # database decides what the customer actually pays.
            promo_code = data.get("promo_code")
            promo = None
            if promo_code:
                promo, _ = await validate_promo(
                    session,
                    promo_code,
                    user_id=callback.from_user.id,
                    price=price,
                )

            original_price = price
            promo_discount = 0
            bonus_days = bonus_gb = 0
            if promo:
                promo_discount = promo.discount
                bonus_days = promo.bonus_days
                bonus_gb = promo.bonus_gb
                price = max(price - promo_discount, 0)

            discount_applied = False
            if await is_discount_eligible(session, user):
                payable, _, discount_applied = apply_purchase_discount(price)
                price = payable

            order = Order(
                user_id=callback.from_user.id,
                days=days + bonus_days,
                traffic_gb=traffic_gb + bonus_gb,
                price=price,
                original_price=original_price,
                plan_id=plan_id,
                referral_discount_applied=discount_applied,
                device_limit=max(int(devices or 1), 1),
                promo_code=promo.code if promo else None,
                promo_discount=promo_discount,
                promo_bonus_days=bonus_days,
                promo_bonus_gb=bonus_gb,
                status="pending",
            )
            session.add(order)
            if promo:
                await reserve_promo(session, await get_promo(session, promo.code))
            await session.commit()
            await session.refresh(order)

        await callback.message.delete()
        await prompt_payment_methods(
            callback.bot, callback.from_user.id, order, state
        )

        logger.info(f"Order {order.id} created by user {callback.from_user.id}")
    
    except Exception as e:
        rate_limiter.reset_user(callback.from_user.id, "create_order")
        logger.error(f"Error creating order for user {callback.from_user.id}: {e}")
        await callback.message.edit_text(
            "در ثبت سفارش خطایی رخ داد. دوباره تلاش کنید."
        )
        await callback.bot.send_message(
            chat_id=callback.from_user.id,
            text=get_text("start"),
            reply_markup=user_main_menu(callback.from_user.id),
        )
        await state.clear()
        await callback.answer("خطا در ثبت سفارش", show_alert=True)
        return
    
    await callback.answer()


@router.callback_query(WalletPayStates.choosing, F.data == "wallet_pay:no")
async def wallet_pay_no(callback: CallbackQuery, state: FSMContext):
    """Legacy keyboard: card payment."""
    await _run_card_payment(callback, state)


@router.callback_query(WalletPayStates.choosing, F.data == "pay:card")
async def pay_card(callback: CallbackQuery, state: FSMContext):
    """Pay by card transfer."""
    await _run_card_payment(callback, state)


async def _run_card_payment(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    order_id = data.get("order_id")
    if not order_id:
        await callback.answer(get_text("error_general"), show_alert=True)
        await state.clear()
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Order).where(Order.id == order_id))
        order = result.scalar_one_or_none()
        if not order or order.user_id != callback.from_user.id:
            await callback.answer(get_text("error_general"), show_alert=True)
            await state.clear()
            return
        if order.status != "pending":
            await callback.answer("این سفارش قابل پرداخت نیست.", show_alert=True)
            await state.clear()
            return

    try:
        await callback.message.delete()
    except Exception:
        pass

    await send_card_payment_instructions(
        callback.bot, callback.from_user.id, order, state, wallet_amount=0
    )
    await callback.answer()


@router.callback_query(WalletPayStates.choosing, F.data == "wallet_pay:yes")
async def wallet_pay_yes(callback: CallbackQuery, state: FSMContext):
    """Legacy keyboard: wallet payment."""
    await _run_wallet_payment(callback, state)


@router.callback_query(WalletPayStates.choosing, F.data == "pay:wallet")
async def pay_wallet(callback: CallbackQuery, state: FSMContext):
    """Pay from the wallet balance."""
    await _run_wallet_payment(callback, state)


async def _run_wallet_payment(callback: CallbackQuery, state: FSMContext) -> None:
    from app.utils.logger import logger

    data = await state.get_data()
    order_id = data.get("order_id")
    if not order_id:
        await callback.answer(get_text("error_general"), show_alert=True)
        await state.clear()
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Order).where(Order.id == order_id))
        order = result.scalar_one_or_none()
        if not order or order.user_id != callback.from_user.id:
            await callback.answer(get_text("error_general"), show_alert=True)
            await state.clear()
            return
        if order.status != "pending":
            await callback.answer("این سفارش قابل پرداخت نیست.", show_alert=True)
            await state.clear()
            return

        balance = await get_balance(session, callback.from_user.id)
        order_price = int(order.price or 0)

        if order_price <= 0:
            # Free order — there is no money to move.
            try:
                await callback.message.delete()
            except Exception:
                pass
            await deliver_free_order(
                callback.bot, callback.from_user.id, order, state
            )
            await callback.answer()
            return

        if balance <= 0:
            try:
                await callback.message.delete()
            except Exception:
                pass
            await callback.bot.send_message(
                chat_id=callback.from_user.id,
                text=get_text("wallet_empty"),
            )
            await send_card_payment_instructions(
                callback.bot, callback.from_user.id, order, state, wallet_amount=0
            )
            await callback.answer()
            return

        debit_amount = min(balance, order_price)
        try:
            new_balance = await debit_balance(
                session, callback.from_user.id, debit_amount
            )
        except InsufficientBalanceError:
            await callback.answer(
                "موجودی کیف پول کافی نیست.",
                show_alert=True,
            )
            return
        except ValueError as exc:
            from app.utils.logger import logger

            logger.error("Wallet debit failed for order %s: %s", order.id, exc)
            await callback.answer("خطا در برداشت از کیف پول.", show_alert=True)
            return

        order.wallet_debit = debit_amount

        if debit_amount >= order.price:
            # Full wallet cover
            order.status = "paid"
            payment = Payment(
                order_id=order.id,
                user_id=order.user_id,
                receipt_file_id="wallet",
                status="pending",
            )
            session.add(payment)
            await session.commit()
            await session.refresh(payment)
            await session.refresh(order)

            try:
                await callback.message.delete()
            except Exception:
                pass

            await callback.bot.send_message(
                chat_id=callback.from_user.id,
                text=get_text(
                    "wallet_pay_full",
                    price=order.price,
                    balance=new_balance,
                ),
                reply_markup=user_main_menu(callback.from_user.id),
            )

            try:
                await fulfill_full_wallet_order(
                    callback.bot, session, payment, order
                )
            except Exception as exc:
                logger.error(
                    "Failed to fulfill wallet order %s: %s", order.id, exc
                )
                await callback.bot.send_message(
                    chat_id=callback.from_user.id,
                    text=(
                        "پرداخت ثبت شد اما فعال‌سازی خودکار ناموفق بود. "
                        "ادمین به‌زودی اکانت را ارسال می‌کند."
                    ),
                )
                await notify_admins_wallet_manual(callback.bot, payment, order)

            await state.clear()
            await callback.answer()
            return

        # Partial — difference by card
        await session.commit()
        await session.refresh(order)

    try:
        await callback.message.delete()
    except Exception:
        pass

    await send_card_payment_instructions(
        callback.bot,
        callback.from_user.id,
        order,
        state,
        wallet_amount=debit_amount,
    )
    await callback.answer()



@router.callback_query(WalletPayStates.choosing, F.data == "pay:stars")
async def pay_stars(callback: CallbackQuery, state: FSMContext):
    """Send a Telegram Stars invoice for the order."""
    data = await state.get_data()
    order_id = data.get("order_id")
    if not order_id:
        await callback.answer(get_text("error_general"), show_alert=True)
        await state.clear()
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Order).where(Order.id == order_id))
        order = result.scalar_one_or_none()
        if not order or order.user_id != callback.from_user.id:
            await callback.answer(get_text("error_general"), show_alert=True)
            await state.clear()
            return
        if order.status != "pending":
            await callback.answer("این سفارش قابل پرداخت نیست.", show_alert=True)
            await state.clear()
            return

        stars, rate = await build_stars_invoice(session, order)
        plan = await get_plan(session, order.plan_id) if order.plan_id else None

    if stars <= 0:
        await callback.answer(get_text("stars_expired"), show_alert=True)
        return

    try:
        await send_stars_invoice(
            callback.bot,
            callback.from_user.id,
            order,
            stars=stars,
            plan_name=plan["name"] if plan else "",
        )
    except Exception as exc:
        from app.utils.logger import logger

        logger.error("Could not send stars invoice for order %s: %s", order.id, exc)
        await callback.answer("ارسال فاکتور استارز ناموفق بود.", show_alert=True)
        return

    await callback.answer()
    await callback.bot.send_message(
        chat_id=callback.from_user.id,
        text=get_text(
            "stars_invoice",
            stars=stars,
            toman=stars * rate,
        ),
    )


@router.pre_checkout_query()
async def stars_pre_checkout(query):
    """Telegram asks before charging — approve it and let the payment settle."""
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def stars_paid(message: Message):
    """Telegram confirmed a Stars payment: mark the order paid and deliver it."""
    from app.utils.logger import logger

    payment_info = message.successful_payment
    order_id = order_id_from_payload(payment_info.invoice_payload)
    if order_id is None:
        logger.warning("Stars payment with an unknown payload: %s", payment_info.invoice_payload)
        return

    charge_id = payment_info.telegram_payment_charge_id

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Order).where(Order.id == order_id))
        order = result.scalar_one_or_none()
        if not order or order.user_id != message.from_user.id:
            logger.warning("Stars payment for an unknown order: %s", order_id)
            return
        if order.status not in ("pending", "paid"):
            logger.info("Stars payment for an order that is already handled: %s", order_id)
            return

        existing = (
            await session.execute(
                select(Payment).where(
                    Payment.order_id == order.id,
                    Payment.status.in_(("pending", "approved")),
                )
            )
        ).scalar_one_or_none()
        if existing:
            logger.info("Order %s already has a payment", order.id)
            return

        order.status = "paid"
        payment = Payment(
            order_id=order.id,
            user_id=order.user_id,
            receipt_file_id=charge_id or "stars",
            status="pending",
            method="stars",
            telegram_charge_id=charge_id,
        )
        session.add(payment)
        await session.commit()
        await session.refresh(payment)
        await session.refresh(order)

    await message.answer(get_text("stars_success"))

    try:
        async with AsyncSessionLocal() as session:
            await fulfill_paid_order_for_user(message.bot, session, order, payment)
    except Exception as exc:
        logger.error("Failed to deliver stars order %s: %s", order.id, exc)
        await message.answer(
            "پرداخت ثبت شد اما فعال‌سازی خودکار ناموفق بود. ادمین به‌زودی اکانت را می‌فرستد."
        )


async def fulfill_paid_order_for_user(
    bot, session: AsyncSession, order: Order, payment: Payment
) -> None:
    """Deliver a paid order (auto provision, inventory, or ask the admin)."""
    from app.bot.handlers.admin import send_config_to_user, send_renewal_to_user
    from app.services.order_fulfillment import fulfill_paid_order

    plan = await get_plan(session, order.plan_id) if order.plan_id else None
    plan_name = plan["name"] if plan else None

    fulfill = await fulfill_paid_order(session, payment, order, None, plan_name)
    if fulfill.kind in ("renewal_auto", "renewal_db") and fulfill.vpn_account:
        await send_renewal_to_user(bot, order.user_id, fulfill.vpn_account, order)
        return
    if fulfill.kind in ("new_auto", "new_inventory") and fulfill.config_text:
        await send_config_to_user(bot, order.user_id, fulfill.config_text)
        return
    await notify_admins_wallet_manual(bot, payment, order)


@router.message(Command("usage"))
async def usage_command(message: Message, state: FSMContext):
    """Show the customer's own quota, usage and expiry."""
    await send_usage(message, message.from_user.id)


@router.callback_query(F.data == "usage:qr")
async def usage_qr(callback: CallbackQuery):
    """Send the subscription link as a QR code."""
    from app.utils.logger import logger

    async with AsyncSessionLocal() as session:
        panel = await get_panel_settings(session)
        accounts = await _load_db_accounts(session, callback.from_user.id)

    link = ""
    for account in accounts:
        if account.subscription_path:
            link = account.subscription_path
            break

    if not link:
        await callback.answer(get_text("usage_none"), show_alert=True)
        return

    try:
        png = subscription_qr_png(link)
    except Exception as exc:
        logger.error("Could not render a subscription QR: %s", exc)
        await callback.answer("ساخت کد QR ناموفق بود.", show_alert=True)
        return

    await callback.answer()
    await callback.bot.send_photo(
        chat_id=callback.from_user.id,
        photo=png,
        caption=get_text("usage_qr_caption"),
    )


async def send_usage(message: Message, user_id: int) -> None:
    """Send the usage summary for one customer."""
    async with AsyncSessionLocal() as session:
        accounts = await _load_db_accounts(session, user_id)

    if not accounts:
        await message.answer(
            get_text("usage_none"),
            reply_markup=user_main_menu(user_id),
        )
        return

    account = accounts[0]
    now = datetime.utcnow()
    expires_at = account.expires_at
    days_left = max((expires_at - now).days, 0)
    text = (
        f"{get_text('usage_title')}\n\n"
        f"حجم کل: {account.traffic_limit_gb} گیگابایت\n"
        f"تاریخ انقضا: {expires_at.strftime('%Y-%m-%d')}\n"
        f"روزهای باقی‌مانده: {days_left}\n"
        f"لینک اشتراک:\n{account.subscription_path}"
    )
    await message.answer(text, reply_markup=usage_keyboard(has_account=True))


@router.message(
    WalletPayStates.choosing,
    F.text.in_(MAIN_MENU_BUTTONS | FLOW_NAV_BUTTONS),
)
async def wallet_pay_menu_interrupt(message: Message, state: FSMContext):
    from app.bot.menu_dispatch import dispatch_main_menu

    await abandon_current_order_if_any(state, message.from_user.id)
    # BACK/CANCEL are not main-menu actions; leave the wallet-pay step cleanly.
    if message.text in FLOW_NAV_BUTTONS:
        await state.clear()
        await message.answer(
            get_text("start"),
            reply_markup=user_main_menu(message.from_user.id),
        )
        return
    await dispatch_main_menu(message, state)


@router.callback_query(F.data == "back:main")
async def back_to_main(callback: CallbackQuery, state: FSMContext):
    """Return to main menu from inline keyboards."""
    await abandon_current_order_if_any(state, callback.from_user.id)
    await state.clear()
    await callback.message.delete()
    await callback.bot.send_message(
        chat_id=callback.from_user.id,
        text=get_text("start"),
        reply_markup=user_main_menu(callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(F.data == "back:plans")
async def back_to_plans(callback: CallbackQuery, state: FSMContext):
    """Return to plans list from plan confirmation."""
    await state.clear()
    await callback.message.delete()
    await send_plans_menu(callback.bot, callback.from_user.id, callback.from_user)
    await callback.answer()


@router.callback_query(F.data == "back:custom_traffic")
async def back_to_custom_traffic(callback: CallbackQuery, state: FSMContext):
    """Return to custom traffic step from confirmation."""
    data = await state.get_data()
    if "days" not in data:
        await callback.answer("اطلاعات سفارش پیدا نشد.", show_alert=True)
        return

    await state.set_state(CustomPlanStates.waiting_for_traffic)
    await callback.message.delete()
    await callback.bot.send_message(
        chat_id=callback.from_user.id,
        text=get_text("custom_plan_traffic"),
        reply_markup=flow_nav_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "cancel_order")
async def cancel_order(callback: CallbackQuery, state: FSMContext):
    """Cancel order / renew confirmation."""
    await abandon_current_order_if_any(state, callback.from_user.id)
    await state.clear()
    await callback.message.delete()
    await callback.bot.send_message(
        chat_id=callback.from_user.id,
        text=get_text("start"),
        reply_markup=user_main_menu(callback.from_user.id)
    )
    await callback.answer()


@router.message(PaymentStates.waiting_for_receipt, F.text == BACK_BUTTON)
async def payment_back(message: Message, state: FSMContext):
    data = await state.get_data()
    order_id = data.get("order_id")
    new_balance = await refund_wallet_on_cancel(order_id, message.from_user.id)
    await state.clear()
    note = ""
    if order_id:
        note = f"\n\nسفارش #{order_id} لغو شد."
    if new_balance is not None:
        note += "\n" + get_text("wallet_refund_on_reject", balance=new_balance)
    await message.answer(
        get_text("start") + note,
        reply_markup=user_main_menu(message.from_user.id),
    )


@router.message(PaymentStates.waiting_for_receipt, F.text == CANCEL_BUTTON)
async def payment_cancel(message: Message, state: FSMContext):
    await payment_back(message, state)


@router.message(PaymentStates.waiting_for_receipt, F.photo)
async def receive_receipt(message: Message, state: FSMContext):
    """Receive payment receipt"""
    data = await state.get_data()
    order_id = data.get("order_id")
    
    if not order_id:
        await message.answer(get_text("error_general"))
        await state.clear()
        return
    
    photo = message.photo[-1]
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Order).where(Order.id == order_id))
        order = result.scalar_one_or_none()

        if not order or order.user_id != message.from_user.id:
            await message.answer(get_text("error_general"))
            await state.clear()
            return

        if order.status not in ("pending", "paid"):
            await message.answer(
                "برای این سفارش نمی‌توان رسید فرستاد.",
                reply_markup=user_main_menu(message.from_user.id),
            )
            await state.clear()
            return

        # One pending (or already approved) payment per order
        existing_result = await session.execute(
            select(Payment).where(
                Payment.order_id == order_id,
                Payment.status.in_(("pending", "approved")),
            )
        )
        existing_payment = existing_result.scalar_one_or_none()
        if existing_payment:
            if existing_payment.status == "approved":
                await message.answer(
                    "این سفارش قبلاً تایید شده است.",
                    reply_markup=user_main_menu(message.from_user.id),
                )
            else:
                await message.answer(
                    "رسید قبلی شما هنوز در حال بررسی است.",
                    reply_markup=user_main_menu(message.from_user.id),
                )
            await state.clear()
            return

        # Ensure user row exists for FK (e.g. edge cases)
        await get_or_create_user(session, message.from_user)
        await session.flush()

        order.status = "paid"
        
        payment = Payment(
            order_id=order_id,
            user_id=message.from_user.id,
            receipt_file_id=photo.file_id,
            status="pending"
        )
        session.add(payment)
        await session.commit()
        await session.refresh(payment)
        
        await message.answer(
            get_text("receipt_received"),
            reply_markup=user_main_menu(message.from_user.id)
        )
        
        from app.bot.keyboards.admin import payment_review_keyboard

        wallet_debit = int(order.wallet_debit or 0)
        wallet_note = ""
        if wallet_debit > 0:
            difference = order.price - wallet_debit
            wallet_note = (
                f"\nاز کیف پول: {wallet_debit:,} تومان"
                f"\nمبلغ رسید (باید واریز شده باشد): {difference:,} تومان"
            )

        admin_text = get_text(
            "admin_new_payment",
            user_id=message.from_user.id,
            username=message.from_user.username or "بدون نام کاربری",
            order_id=order.id,
            days=order.days,
            traffic=order.traffic_gb,
            price=order.price,
            wallet_note=wallet_note,
            renewal_note=(
                f"\nتمدید اکانت #{order.renew_vpn_account_id}"
                if order.renew_vpn_account_id
                else ""
            ),
        )
        
        for admin_id in settings.ADMIN_IDS:
            try:
                await message.bot.send_photo(
                    chat_id=admin_id,
                    photo=photo.file_id,
                    caption=admin_text,
                    reply_markup=payment_review_keyboard(payment.id)
                )
            except Exception as exc:
                from app.utils.logger import logger
                logger.warning("Failed to notify admin %s about payment %s: %s", admin_id, payment.id, exc)
    
    await state.clear()


_MENU_BUTTONS = MAIN_MENU_BUTTONS | FLOW_NAV_BUTTONS


@router.message(PaymentStates.waiting_for_receipt, ~F.text.in_(_MENU_BUTTONS))
async def receive_receipt_invalid(message: Message):
    """Remind user to send a photo while waiting for receipt."""
    await message.answer(
        "لطفاً تصویر رسید پرداخت را ارسال کنید.\n"
        f"یا از «{BACK_BUTTON}» برای برگشت به منو استفاده کنید."
    )


@router.message(F.text.in_(button_texts(BTN_MY_ORDERS)))
async def my_orders(message: Message, state: FSMContext):
    """Show user orders"""
    await abandon_current_order_if_any(state, message.from_user.id)
    await state.clear()
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Order)
            .where(Order.user_id == message.from_user.id)
            .order_by(Order.created_at.desc())
            .limit(20)
        )
        orders = result.scalars().all()
        
        if not orders:
            await message.answer(
                get_text("no_orders"),
                reply_markup=user_main_menu(message.from_user.id),
            )
            return
        
        text = get_text("my_orders") + "\n\n"
        
        status_map = ORDER_STATUS_LABELS
        
        for order in orders:
            discount_tag = ""
            if order.referral_discount_applied and order.original_price:
                discount_tag = (
                    f" (تخفیف معرفی از {order.original_price:,})"
                )
            text += get_text(
                "order_status",
                order_id=order.id,
                days=order.days,
                traffic=order.traffic_gb,
                price=order.price,
                discount_tag=discount_tag,
                status=status_map.get(order.status, order.status),
                created_at=order.created_at.strftime("%Y-%m-%d %H:%M")
            ) + "\n"
        
        await message.answer(
            text,
            reply_markup=user_main_menu(message.from_user.id),
        )


def format_panel_account_text(
    detail: dict,
    subscription_base_url: str | None,
    *,
    now: datetime | None = None,
) -> str:
    client = client_record(detail)
    now = now or datetime.now(timezone.utc)
    total_bytes = int(client.get("totalGB") or 0)
    used = used_bytes(detail)
    expiry_ms = int(client.get("expiryTime") or 0)
    expires_at = (
        datetime.fromtimestamp(expiry_ms / 1000, timezone.utc) if expiry_ms > 0 else None
    )

    if expires_at and expires_at <= now:
        status = "منقضی شده"
    elif total_bytes > 0 and used >= total_bytes:
        status = "حجم تمام شده"
    elif not client.get("enable", True):
        status = "غیرفعال"
    else:
        status = "فعال"

    if total_bytes > 0:
        remaining_bytes = max(total_bytes - used, 0)
        traffic_text = (
            f"حجم کل: {format_gb(total_bytes)} گیگابایت\n"
            f"مصرف‌شده: {format_gb(used)} گیگابایت\n"
            f"باقی‌مانده: {format_gb(remaining_bytes)} گیگابایت"
        )
    else:
        traffic_text = f"حجم: نامحدود\nمصرف‌شده: {format_gb(used)} گیگابایت"

    if expires_at:
        expires_text = expires_at.strftime("%Y-%m-%d")
        days_left = max((expires_at - now).days, 0)
        expiry_details = f"انقضا: {expires_text}\nروزهای باقی‌مانده: {days_left}"
    else:
        expiry_details = "انقضا: نامحدود"

    sub_id = client.get("subId")
    subscription_url = (
        build_subscription_url(subscription_base_url, str(sub_id))
        if subscription_base_url and sub_id
        else "در دسترس نیست"
    )

    return (
        "اطلاعات اکانت شما از پنل 3x-ui:\n\n"
        f"شناسه: {client.get('email') or '—'}\n"
        f"{traffic_text}\n"
        f"{expiry_details}\n"
        f"وضعیت: {status}\n"
        f"محدودیت IP همزمان: {client.get('limitIp') or 0}\n"
        f"لینک اشتراک:\n{subscription_url}\n\n"
        "برای تمدید، دکمه زیر را بزنید."
    )


async def _load_db_accounts(session: AsyncSession, user_id: int) -> list[VPNAccount]:
    result = await session.execute(
        select(VPNAccount)
        .where(VPNAccount.user_id == user_id)
        .order_by(VPNAccount.created_at.desc())
        .limit(20)
    )
    return list(result.scalars().all())


def format_db_accounts_text(
    accounts: list[VPNAccount],
    now: datetime | None = None,
) -> str:
    """Stored accounts (manual/inventory deliveries) — the panel does not own these."""
    now = now or datetime.utcnow()
    parts = ["اکانت‌های شما:", ""]
    for account in accounts:
        is_expired = account.expires_at < now
        days_left = max((account.expires_at - now).days, 0)
        if is_expired:
            status = "منقضی شده"
        elif account.is_active:
            status = "فعال"
        else:
            status = "غیرفعال"
        parts.append(
            f"شماره سفارش: #{account.order_id}\n"
            f"حجم: {account.traffic_limit_gb} گیگابایت\n"
            f"روزهای باقی‌مانده: {days_left} روز\n"
            f"تاریخ انقضا: {account.expires_at.strftime('%Y-%m-%d')}\n"
            f"وضعیت: {status}\n"
            f"لینک اشتراک:\n{account.subscription_path}\n"
            f"{'─' * 30}"
        )
    parts.append("برای تمدید، دکمه زیر را بزنید.")
    return "\n".join(parts)


async def send_accounts_list(event: Message | CallbackQuery) -> None:
    from app.utils.logger import logger

    user_id = event.from_user.id
    bot = event.bot
    chat_id = event.chat.id if isinstance(event, Message) else event.message.chat.id

    async with AsyncSessionLocal() as session:
        panel = await get_panel_settings(session)
        panel_ready = has_panel_credentials(panel)
        subscription_base_url = panel.subscription_base_url
        detail = None
        if panel_ready:
            try:
                async with xui_client_for_panel(panel) as client:
                    detail = await client.get_client(str(user_id))
                    # Read the subscription base from the panel so a change to
                    # the panel's subscription settings is picked up here.
                    try:
                        subscription_base_url = await client.get_subscription_base_url()
                    except Exception:
                        pass
            except Exception as exc:
                # A panel outage must not hide the accounts we already delivered.
                logger.warning("Panel lookup failed for user %s: %s", user_id, exc)
                detail = None

        if detail and detail.get("client"):
            await bot.send_message(
                chat_id=chat_id,
                text=format_panel_account_text(detail, subscription_base_url),
                reply_markup=accounts_list_keyboard(client_email=str(user_id)),
            )
            logger.info("User %s viewed panel account details", user_id)
            return

        # No panel (manual mode) or no panel client for this user: fall back to
        # the stored accounts so the subscription link is always reachable.
        accounts = await _load_db_accounts(session, user_id)
        if accounts:
            await bot.send_message(
                chat_id=chat_id,
                text=format_db_accounts_text(accounts),
                reply_markup=accounts_list_keyboard(
                    account_ids=[account.id for account in accounts]
                ),
            )
            logger.info("User %s viewed %s stored account(s)", user_id, len(accounts))
            return

    await bot.send_message(
        chat_id=chat_id,
        text=(
            "هنوز اکانتی ندارید.\n\n"
            "برای خرید، از منو «خرید پلن» استفاده کنید."
        ),
        reply_markup=user_main_menu(user_id),
    )


@router.message(F.text.in_(button_texts(BTN_MY_ACCOUNTS)))
async def my_accounts(message: Message, state: FSMContext):
    """Show user's VPN accounts"""
    await abandon_current_order_if_any(state, message.from_user.id)
    await state.clear()
    try:
        await send_accounts_list(message)
    except Exception as e:
        from app.utils.logger import logger
        logger.error("Error showing accounts for user %s: %s", message.from_user.id, e)
        await message.answer(
            "خطایی رخ داد. دوباره تلاش کنید.",
            reply_markup=user_main_menu(message.from_user.id),
        )


@router.callback_query(F.data == "back:accounts")
async def back_to_accounts(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    try:
        await send_accounts_list(callback)
    except Exception:
        await callback.bot.send_message(
            chat_id=callback.from_user.id,
            text=get_text("start"),
            reply_markup=user_main_menu(callback.from_user.id),
        )
    await callback.answer()


async def show_renew_plans(
    callback: CallbackQuery,
    state: FSMContext,
    account: VPNAccount,
) -> None:
    await state.clear()
    await state.update_data(renew_vpn_account_id=account.id)
    async with AsyncSessionLocal() as session:
        plans = await list_active_plans(session)
    await callback.message.edit_text(
        f"تمدید اکانت سفارش #{account.order_id}\n\n"
        "یک پلن برای تمدید انتخاب کنید:",
        reply_markup=renew_plans_keyboard(plans, account.id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("renew_account:"))
async def renew_account_start(callback: CallbackQuery, state: FSMContext):
    try:
        vpn_account_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("اکانت پیدا نشد.", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(VPNAccount).where(
                VPNAccount.id == vpn_account_id,
                VPNAccount.user_id == callback.from_user.id,
            )
        )
        account = result.scalar_one_or_none()

    if not account:
        await callback.answer("اکانت پیدا نشد.", show_alert=True)
        return
    await show_renew_plans(callback, state, account)


@router.callback_query(F.data.startswith("renew_client:"))
async def renew_panel_client_start(callback: CallbackQuery, state: FSMContext):
    client_email = callback.data.split(":", 1)[1]
    if client_email != str(callback.from_user.id):
        await callback.answer("اکانت پیدا نشد.", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        # The panel keeps one client per user, so renew the account row that
        # belongs to that client — not simply the newest row of the user.
        result = await session.execute(
            select(VPNAccount)
            .where(
                VPNAccount.user_id == callback.from_user.id,
                VPNAccount.config_ref == "xui-auto",
            )
            .order_by(VPNAccount.created_at.desc())
            .limit(1)
        )
        account = result.scalar_one_or_none()

        if account is None:
            result = await session.execute(
                select(VPNAccount)
                .where(VPNAccount.user_id == callback.from_user.id)
                .order_by(VPNAccount.created_at.desc())
                .limit(1)
            )
            account = result.scalar_one_or_none()

    if not account:
        await callback.answer("برای این اکانت سفارش تمدید پیدا نشد.", show_alert=True)
        return
    await show_renew_plans(callback, state, account)


@router.callback_query(F.data.startswith("renew_plan:"))
async def renew_select_plan(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    try:
        vpn_account_id = int(parts[1])
        plan_id = parts[2]
    except (IndexError, ValueError):
        await callback.answer("پلن پیدا نشد.", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        plan = await get_plan(session, plan_id, active_only=True)

    if not plan:
        await callback.answer("پلن پیدا نشد.", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(VPNAccount).where(
                VPNAccount.id == vpn_account_id,
                VPNAccount.user_id == callback.from_user.id,
            )
        )
        account = result.scalar_one_or_none()

    if not account:
        await callback.answer("اکانت پیدا نشد.", show_alert=True)
        return

    await state.update_data(
        renew_vpn_account_id=vpn_account_id,
        plan_id=plan_id,
        days=plan["days"],
        traffic=plan["traffic"],
        price=plan["price"],
    )

    await callback.message.edit_text(
        get_text(
            "renewal_confirm",
            order_id=account.order_id,
            name=plan["name"],
            days=plan["days"],
            traffic=plan["traffic"],
            price=plan["price"],
        ),
        reply_markup=confirm_renew_keyboard(vpn_account_id),
    )
    await callback.answer()


@router.callback_query(F.data == "confirm_renew_order")
async def confirm_renew_order(callback: CallbackQuery, state: FSMContext):
    from app.utils.logger import logger
    from app.utils.rate_limiter import rate_limiter

    can_proceed, remaining = rate_limiter.check_limit(
        callback.from_user.id, "create_renew_order", seconds=60
    )
    if not can_proceed:
        await callback.answer(
            f"{remaining} ثانیه صبر کنید و بعد سفارش جدید ثبت کنید.",
            show_alert=True,
        )
        return

    data = await state.get_data()
    renew_vpn_account_id = data.get("renew_vpn_account_id")
    async with AsyncSessionLocal() as session:
        resolved = await resolve_order_pricing(session, data)

    if not renew_vpn_account_id or not resolved:
        await callback.answer("اطلاعات تمدید نامعتبر است.", show_alert=True)
        await state.clear()
        return

    plan_id, days, traffic_gb, price = resolved

    async with AsyncSessionLocal() as session:
        acc_result = await session.execute(
            select(VPNAccount).where(
                VPNAccount.id == renew_vpn_account_id,
                VPNAccount.user_id == callback.from_user.id,
            )
        )
        if not acc_result.scalar_one_or_none():
            await callback.answer("اکانت پیدا نشد.", show_alert=True)
            await state.clear()
            return

        await get_or_create_user(session, callback.from_user)
        order = Order(
            user_id=callback.from_user.id,
            days=days,
            traffic_gb=traffic_gb,
            price=price,
            original_price=price,
            plan_id=plan_id,
            renew_vpn_account_id=renew_vpn_account_id,
            status="pending",
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)

    await callback.message.delete()
    await prompt_wallet_payment(callback.bot, callback.from_user.id, order, state)
    logger.info(
        "Renewal order %s created for account %s by user %s",
        order.id,
        renew_vpn_account_id,
        callback.from_user.id,
    )
    await callback.answer()
