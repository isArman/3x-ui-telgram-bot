from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery
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
)
from app.bot.states import CustomPlanStates, PaymentStates, WalletPayStates
from app.bot.keyboards.user import (
    main_menu_keyboard,
    plans_keyboard,
    confirm_order_keyboard,
    confirm_renew_keyboard,
    accounts_list_keyboard,
    renew_plans_keyboard,
    flow_nav_keyboard,
    wallet_pay_keyboard,
)
from app.services.users import get_or_create_user
from app.services.wallet import (
    InsufficientBalanceError,
    debit_balance,
    get_balance,
)
from app.services.order_fulfillment import fulfill_paid_order
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
from app.services.bot_settings import get_card_details
from app.services.plans_catalog import get_plan, get_pricing, list_active_plans

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
        return text + "هنوز پلن آماده‌ای تعریف نشده است.\n"
    for plan in plans:
        text += get_text("plan_details", **plan) + "\n"
    return text


async def referral_plans_hint_for(session: AsyncSession, tg_user) -> str:
    user, _ = await get_or_create_user(session, tg_user)
    if await is_discount_eligible(session, user):
        return get_text("referral_plans_hint")
    return ""


async def price_block_for_tg_user(session: AsyncSession, tg_user, list_price: int) -> str:
    user, _ = await get_or_create_user(session, tg_user)
    eligible = await is_discount_eligible(session, user)
    payable, original, applied = preview_discounted_price(
        list_price, eligible=eligible
    )
    return format_price_block(original, payable=payable, applied=applied)


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

    Ready-made plans: days/traffic/price come from shop_plans via plan_id.
    Custom plans: recompute price from days/traffic + pricing formula.

    Returns (plan_id, days, traffic_gb, price) or None if invalid.
    """
    plan_id = data.get("plan_id")
    if plan_id:
        plan = await get_plan(session, plan_id, active_only=True)
        if not plan:
            return None
        return plan_id, int(plan["days"]), int(plan["traffic"]), int(plan["price"])

    try:
        days = int(data["days"])
        traffic = int(data["traffic"])
    except (KeyError, TypeError, ValueError):
        return None

    if not 1 <= days <= 365 or not 1 <= traffic <= 500:
        return None

    pricing = await get_pricing(session)
    return None, days, traffic, calculate_custom_price(days, traffic, pricing)


async def get_or_create_user_from_message(
    session: AsyncSession, message: Message
) -> tuple[User, bool]:
    return await get_or_create_user(session, message.from_user)


async def prompt_wallet_payment(bot, user_id: int, order: Order, state: FSMContext) -> None:
    """Ask the user whether to pay with wallet after order creation."""
    async with AsyncSessionLocal() as session:
        balance = await get_balance(session, user_id)

    await bot.send_message(
        chat_id=user_id,
        text=get_text(
            "wallet_pay_prompt",
            order_id=order.id,
            price_lines=format_order_price_lines(order),
            balance=balance,
        ),
        reply_markup=wallet_pay_keyboard(),
    )
    await state.update_data(order_id=order.id)
    await state.set_state(WalletPayStates.choosing)


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
        # #region agent log
        from app.utils.debug_ndjson import agent_log

        agent_log(
            "C",
            "user.py:send_card_payment_instructions",
            "card missing after possible wallet debit",
            {
                "user_id": user_id,
                "order_id": order.id,
                "wallet_amount": wallet_amount,
                "order_wallet_debit": int(order.wallet_debit or 0),
            },
            run_id="post-fix",
        )
        # #endregion
        if int(order.wallet_debit or 0) > 0 or wallet_amount > 0:
            await refund_wallet_on_cancel(order.id, user_id)
        await bot.send_message(
            chat_id=user_id,
            text=(
                "❌ اطلاعات کارت بانکی هنوز توسط ادمین تنظیم نشده است.\n"
                "لطفاً بعداً دوباره تلاش کنید."
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
        "💳 سفارش پرداخت‌شده با کیف پول — نیاز به لینک دستی\n\n"
        f"👤 کاربر: {order.user_id}\n"
        f"🔢 سفارش: #{order.id}\n"
        f"📦 {order.days} روز | {order.traffic_gb} گیگابایت\n"
        f"💰 مبلغ: {order.price:,} تومان\n"
        f"💳 از کیف پول: {wallet_debit:,} تومان\n\n"
        "روی تایید بزنید و لینک را ارسال کنید."
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


async def fulfill_full_wallet_order(bot, session, payment: Payment, order: Order) -> None:
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
        text=(
            "✅ پرداخت از کیف پول ثبت شد.\n"
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
    """Start command handler — supports /start <referral_code>."""
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

    await message.answer(
        text,
        reply_markup=user_main_menu(message.from_user.id),
    )


@router.message(F.text == BTN_REFERRAL)
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


@router.message(F.text == BTN_BUY_PLAN)
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


@router.callback_query(F.data.startswith("plan:"))
async def select_plan(callback: CallbackQuery, state: FSMContext):
    """Handle plan selection"""
    plan_id = callback.data.split(":")[1]
    async with AsyncSessionLocal() as session:
        plan = await get_plan(session, plan_id, active_only=True)
        if not plan:
            await callback.answer("پلن یافت نشد!", show_alert=True)
            return
        price_block = await price_block_for_tg_user(
            session, callback.from_user, plan["price"]
        )

    await abandon_current_order_if_any(state, callback.from_user.id)
    await state.clear()
    await state.update_data(
        plan_id=plan_id,
        days=plan["days"],
        traffic=plan["traffic"],
        price=plan["price"],
    )

    desc = (plan.get("description") or "").strip()
    desc_block = f"{desc}\n" if desc else ""

    await callback.message.edit_text(
        get_text(
            "plan_confirm",
            name=plan["name"],
            days=plan["days"],
            traffic=plan["traffic"],
            price_block=price_block,
            description=desc_block,
        ),
        reply_markup=confirm_order_keyboard("back:plans"),
    )
    await callback.answer()


@router.message(F.text == BTN_CUSTOM_PLAN)
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
        days = int(message.text)
        if not 1 <= days <= 365:
            await message.answer(get_text("error_out_of_range"))
            return

        await state.update_data(days=days)
        await state.set_state(CustomPlanStates.waiting_for_traffic)
        await message.answer(
            get_text("custom_plan_traffic"),
            reply_markup=flow_nav_keyboard(),
        )
    except ValueError:
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
        traffic = int(message.text)
        if not 1 <= traffic <= 500:
            await message.answer(get_text("error_out_of_range"))
            return

        data = await state.get_data()
        days = data["days"]
        async with AsyncSessionLocal() as session:
            pricing = await get_pricing(session)
            price = calculate_custom_price(days, traffic, pricing)
            price_block = await price_block_for_tg_user(
                session, message.from_user, price
            )

        await state.update_data(traffic=traffic, price=price, plan_id=None)

        await message.answer(
            get_text(
                "custom_plan_confirm",
                days=days,
                traffic=traffic,
                price_block=price_block,
            ),
            reply_markup=confirm_order_keyboard("back:custom_traffic"),
        )
        await state.set_state(CustomPlanStates.waiting_for_confirm)
    except ValueError:
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
            f"لطفاً {remaining} ثانیه صبر کنید قبل از ایجاد سفارش جدید.",
            show_alert=True
        )
        logger.warning(f"User {callback.from_user.id} hit rate limit for order creation")
        return
    
    data = await state.get_data()
    async with AsyncSessionLocal() as session:
        resolved = await resolve_order_pricing(session, data)

    if not resolved:
        await callback.answer(
            "اطلاعات سفارش نامعتبر است. لطفاً دوباره پلن را انتخاب کنید.",
            show_alert=True,
        )
        await state.clear()
        return

    plan_id, days, traffic_gb, price = resolved
    
    try:
        async with AsyncSessionLocal() as session:
            user, _ = await get_or_create_user(session, callback.from_user)

            original_price = price
            discount_applied = False
            if await is_discount_eligible(session, user):
                payable, original_price, discount_applied = apply_purchase_discount(
                    price
                )
                price = payable

            order = Order(
                user_id=callback.from_user.id,
                days=days,
                traffic_gb=traffic_gb,
                price=price,
                original_price=original_price,
                plan_id=plan_id,
                referral_discount_applied=discount_applied,
                status="pending",
            )
            session.add(order)
            await session.commit()
            await session.refresh(order)

        await callback.message.delete()
        await prompt_wallet_payment(
            callback.bot, callback.from_user.id, order, state
        )

        logger.info(f"Order {order.id} created by user {callback.from_user.id}")
    
    except Exception as e:
        rate_limiter.reset_user(callback.from_user.id, "create_order")
        logger.error(f"Error creating order for user {callback.from_user.id}: {e}")
        await callback.message.edit_text(
            "خطایی در ایجاد سفارش رخ داد. لطفاً دوباره تلاش کنید."
        )
        await callback.bot.send_message(
            chat_id=callback.from_user.id,
            text=get_text("start"),
            reply_markup=user_main_menu(callback.from_user.id),
        )
        await state.clear()
        await callback.answer("خطا در ایجاد سفارش", show_alert=True)
        return
    
    await callback.answer()


@router.callback_query(WalletPayStates.choosing, F.data == "wallet_pay:no")
async def wallet_pay_no(callback: CallbackQuery, state: FSMContext):
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

        debit_amount = min(balance, order.price)
        try:
            new_balance = await debit_balance(
                session, callback.from_user.id, debit_amount
            )
        except InsufficientBalanceError:
            await callback.answer(
                "موجودی کیف پول کافی نیست. دوباره تلاش کنید.",
                show_alert=True,
            )
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



@router.message(
    WalletPayStates.choosing,
    F.text.in_(MAIN_MENU_BUTTONS | FLOW_NAV_BUTTONS),
)
async def wallet_pay_menu_interrupt(message: Message, state: FSMContext):
    from app.bot.menu_dispatch import dispatch_main_menu

    await abandon_current_order_if_any(state, message.from_user.id)
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
        await callback.answer("اطلاعات سفارش یافت نشد.", show_alert=True)
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
        note = f"\n\n🔢 سفارش #{order_id} لغو شد."
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
                "این سفارش قابل ارسال رسید نیست.",
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
                    "رسید قبلی شما هنوز در انتظار بررسی ادمین است.",
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
                f"\n💳 از کیف پول: {wallet_debit:,} تومان"
                f"\n💵 مبلغ رسید (باید واریز شده باشد): {difference:,} تومان"
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
                f"\n🔄 تمدید اکانت #{order.renew_vpn_account_id}"
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


@router.message(F.text == BTN_MY_ORDERS)
async def my_orders(message: Message, state: FSMContext):
    """Show user orders"""
    await abandon_current_order_if_any(state, message.from_user.id)
    await state.clear()
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Order)
            .where(Order.user_id == message.from_user.id)
            .order_by(Order.created_at.desc())
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


async def send_accounts_list(event: Message | CallbackQuery) -> None:
    from app.utils.logger import logger
    from datetime import datetime

    user_id = event.from_user.id
    bot = event.bot
    chat_id = event.chat.id if isinstance(event, Message) else event.message.chat.id

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(VPNAccount)
            .where(VPNAccount.user_id == user_id)
            .order_by(VPNAccount.created_at.desc())
        )
        accounts = result.scalars().all()

        if not accounts:
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    "شما هنوز هیچ اکانتی ندارید.\n\n"
                    "برای خرید اکانت جدید از منو 'خرید پلن' استفاده کنید."
                ),
                reply_markup=user_main_menu(user_id),
            )
            return

        now = datetime.utcnow()
        text = "💳 اکانت‌های شما:\n\n"

        for account in accounts:
            is_expired = account.expires_at < now
            days_left = (account.expires_at - now).days if not is_expired else 0
            status = "🟢 فعال" if not is_expired and account.is_active else "🔴 منقضی شده"

            text += (
                f"🆔 شماره سفارش: #{account.order_id}\n"
                f"📊 حجم: {account.traffic_limit_gb} گیگابایت\n"
                f"⏱ روزهای باقیمانده: {days_left} روز\n"
                f"📅 تاریخ انقضا: {account.expires_at.strftime('%Y-%m-%d')}\n"
                f"وضعیت: {status}\n"
                f"🔗 لینک اشتراک:\n{account.subscription_path}\n"
                f"{'─' * 30}\n\n"
            )

        text += "برای تمدید، دکمه «🔄 تمدید» زیر را بزنید."
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=accounts_list_keyboard(accounts),
        )
        logger.info("User %s viewed their accounts", user_id)


@router.message(F.text == BTN_MY_ACCOUNTS)
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
            "خطایی رخ داد. لطفاً دوباره تلاش کنید.",
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


@router.callback_query(F.data.startswith("renew_account:"))
async def renew_account_start(callback: CallbackQuery, state: FSMContext):
    vpn_account_id = int(callback.data.split(":")[1])

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(VPNAccount).where(
                VPNAccount.id == vpn_account_id,
                VPNAccount.user_id == callback.from_user.id,
            )
        )
        account = result.scalar_one_or_none()

    if not account:
        await callback.answer("اکانت یافت نشد!", show_alert=True)
        return

    await state.clear()
    await state.update_data(renew_vpn_account_id=vpn_account_id)
    async with AsyncSessionLocal() as session:
        plans = await list_active_plans(session)
    await callback.message.edit_text(
        f"🔄 تمدید اکانت سفارش #{account.order_id}\n\n"
        "یک پلن برای تمدید انتخاب کنید:",
        reply_markup=renew_plans_keyboard(plans, vpn_account_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("renew_plan:"))
async def renew_select_plan(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    vpn_account_id = int(parts[1])
    plan_id = parts[2]
    async with AsyncSessionLocal() as session:
        plan = await get_plan(session, plan_id, active_only=True)

    if not plan:
        await callback.answer("پلن یافت نشد!", show_alert=True)
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
        await callback.answer("اکانت یافت نشد!", show_alert=True)
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
        callback.from_user.id, "create_order", seconds=60
    )
    if not can_proceed:
        await callback.answer(
            f"لطفاً {remaining} ثانیه صبر کنید قبل از سفارش جدید.",
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
            await callback.answer("اکانت یافت نشد!", show_alert=True)
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
