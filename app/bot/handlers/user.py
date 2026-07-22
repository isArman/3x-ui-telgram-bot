from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.texts import get_text
from app.config.settings import settings
from app.config.plans_loader import PLANS, PRICING, get_plan
from app.database.models import User, Order, Payment
from app.database.session import AsyncSessionLocal
from app.bot.constants import (
    BACK_BUTTON,
    BTN_ADMIN_PANEL,
    BTN_BUY_PLAN,
    BTN_CUSTOM_PLAN,
    BTN_MY_ACCOUNTS,
    BTN_MY_ORDERS,
    CANCEL_BUTTON,
    FLOW_NAV_BUTTONS,
    MAIN_MENU_BUTTONS,
    ORDER_STATUS_LABELS,
)
from app.bot.states import CustomPlanStates, PaymentStates
from app.bot.keyboards.user import (
    main_menu_keyboard,
    plans_keyboard,
    confirm_order_keyboard,
    flow_nav_keyboard,
)
from app.services.users import get_or_create_user

router = Router()


def user_main_menu(user_id: int):
    return main_menu_keyboard(is_admin=user_id in settings.ADMIN_IDS)


def build_plans_text() -> str:
    text = get_text("plans_list")
    for plan in PLANS:
        text += get_text("plan_details", **plan) + "\n"
    return text


async def send_plans_menu(bot, chat_id: int) -> None:
    await bot.send_message(
        chat_id=chat_id,
        text=build_plans_text(),
        reply_markup=plans_keyboard(PLANS),
    )


async def send_main_menu_message(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        get_text("start"),
        reply_markup=user_main_menu(message.from_user.id),
    )


def calculate_custom_price(days: int, traffic_gb: int) -> int:
    """Calculate price for custom plan"""
    return (days * PRICING["per_day"]) + (traffic_gb * PRICING["per_gb"])


def resolve_order_pricing(data: dict):
    """
    Resolve authoritative plan pricing on the server.

    Ready-made plans: days/traffic/price come from plans.yaml via plan_id.
    Custom plans: recompute price from days/traffic + pricing formula.

    Returns (plan_id, days, traffic_gb, price) or None if invalid.
    """
    plan_id = data.get("plan_id")
    if plan_id:
        plan = get_plan(plan_id)
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

    return None, days, traffic, calculate_custom_price(days, traffic)


async def get_or_create_user_from_message(session: AsyncSession, message: Message) -> User:
    return await get_or_create_user(session, message.from_user)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    """Start command handler"""
    await state.clear()

    async with AsyncSessionLocal() as session:
        await get_or_create_user_from_message(session, message)
    
    await message.answer(
        get_text("start"),
        reply_markup=user_main_menu(message.from_user.id),
    )


@router.message(F.text == BTN_BUY_PLAN)
async def show_plans(message: Message, state: FSMContext):
    """Show available plans"""
    await state.clear()
    await message.answer(
        build_plans_text(),
        reply_markup=plans_keyboard(PLANS),
    )


@router.callback_query(F.data.startswith("plan:"))
async def select_plan(callback: CallbackQuery, state: FSMContext):
    """Handle plan selection"""
    plan_id = callback.data.split(":")[1]
    plan = get_plan(plan_id)
    
    if not plan:
        await callback.answer("پلن یافت نشد!", show_alert=True)
        return

    await state.clear()
    await state.update_data(
        plan_id=plan_id,
        days=plan["days"],
        traffic=plan["traffic"],
        price=plan["price"],
    )
    
    await callback.message.edit_text(
        get_text("custom_plan_confirm", **plan),
        reply_markup=confirm_order_keyboard("back:plans"),
    )
    await callback.answer()


@router.message(F.text == BTN_CUSTOM_PLAN)
async def custom_plan_start(message: Message, state: FSMContext):
    """Start custom plan flow"""
    # Clear any leftover plan_id from a previously selected ready-made plan
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
        price = calculate_custom_price(days, traffic)

        await state.update_data(traffic=traffic, price=price, plan_id=None)

        await message.answer(
            get_text("custom_plan_confirm", days=days, traffic=traffic, price=price),
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
    resolved = resolve_order_pricing(data)

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
            user = await get_or_create_user(session, callback.from_user)
            
            # Create order with server-resolved pricing
            order = Order(
                user_id=callback.from_user.id,
                days=days,
                traffic_gb=traffic_gb,
                price=price,
                plan_id=plan_id,
                status="pending",
            )
            session.add(order)
            await session.commit()
            await session.refresh(order)

        # Send payment instructions
        await callback.message.delete()
        await callback.bot.send_message(
            chat_id=callback.from_user.id,
            text=get_text(
                "order_created",
                order_id=order.id,
                price=order.price,
                card_number=settings.CARD_NUMBER,
                card_holder=settings.CARD_HOLDER,
            ),
            reply_markup=flow_nav_keyboard(),
        )

        # Set state to wait for receipt
        await state.update_data(order_id=order.id)
        await state.set_state(PaymentStates.waiting_for_receipt)

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


@router.callback_query(F.data == "back:main")
async def back_to_main(callback: CallbackQuery, state: FSMContext):
    """Return to main menu from inline keyboards."""
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
    await send_plans_menu(callback.bot, callback.from_user.id)
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
    """Cancel order"""
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
    await state.clear()
    note = ""
    if order_id:
        note = f"\n\n🔢 سفارش #{order_id} همچنان ثبت است. برای پرداخت دوباره «خرید پلن» را بزنید."
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
        
        admin_text = get_text(
            "admin_new_payment",
            user_id=message.from_user.id,
            username=message.from_user.username or "بدون نام کاربری",
            order_id=order.id,
            days=order.days,
            traffic=order.traffic_gb,
            price=order.price
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
            text += get_text(
                "order_status",
                order_id=order.id,
                days=order.days,
                traffic=order.traffic_gb,
                price=order.price,
                status=status_map.get(order.status, order.status),
                created_at=order.created_at.strftime("%Y-%m-%d %H:%M")
            ) + "\n"
        
        await message.answer(
            text,
            reply_markup=user_main_menu(message.from_user.id),
        )


@router.message(F.text == BTN_MY_ACCOUNTS)
async def my_accounts(message: Message, state: FSMContext):
    """Show user's VPN accounts"""
    await state.clear()
    from app.utils.logger import logger
    from app.database.models import VPNAccount
    from datetime import datetime
    
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(VPNAccount)
                .where(VPNAccount.user_id == message.from_user.id)
                .order_by(VPNAccount.created_at.desc())
            )
            accounts = result.scalars().all()
            
            if not accounts:
                await message.answer(
                    "شما هنوز هیچ اکانتی ندارید.\n\n"
                    "برای خرید اکانت جدید از منو 'خرید پلن' استفاده کنید.",
                    reply_markup=user_main_menu(message.from_user.id)
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
            
            text += "برای تمدید اکانت با ادمین تماس بگیرید."
            
            # No parse_mode: config URLs often contain characters that break Markdown
            await message.answer(
                text,
                reply_markup=user_main_menu(message.from_user.id),
            )
            logger.info(f"User {message.from_user.id} viewed their accounts")
            
    except Exception as e:
        logger.error(f"Error showing accounts for user {message.from_user.id}: {e}")
        await message.answer(
            "خطایی رخ داد. لطفاً دوباره تلاش کنید.",
            reply_markup=user_main_menu(message.from_user.id)
        )
