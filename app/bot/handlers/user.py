import yaml
from pathlib import Path
from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.texts import get_text
from app.config.settings import settings
from app.database.models import User, Order, Payment
from app.database.session import AsyncSessionLocal
from app.bot.states import CustomPlanStates, PaymentStates
from app.bot.keyboards.user import (
    main_menu_keyboard,
    plans_keyboard,
    confirm_order_keyboard,
    cancel_keyboard
)

router = Router()

# Load plans
plans_path = Path(__file__).parent.parent.parent / "config" / "plans.yaml"
with open(plans_path, "r", encoding="utf-8") as f:
    plans_data = yaml.safe_load(f)
    PLANS = plans_data["plans"]
    PRICING = plans_data["pricing"]


def calculate_custom_price(days: int, traffic_gb: int) -> int:
    """Calculate price for custom plan"""
    return (days * PRICING["per_day"]) + (traffic_gb * PRICING["per_gb"])


async def get_or_create_user(session: AsyncSession, message: Message) -> User:
    """Get or create user"""
    result = await session.execute(select(User).where(User.id == message.from_user.id))
    user = result.scalar_one_or_none()
    
    if not user:
        user = User(
            id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name
        )
        session.add(user)
        await session.commit()
    
    return user


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    """Start command handler"""
    await state.clear()
    
    async with AsyncSessionLocal() as session:
        await get_or_create_user(session, message)
    
    await message.answer(
        get_text("start"),
        reply_markup=main_menu_keyboard()
    )


@router.message(F.text == "📦 خرید پلن")
async def show_plans(message: Message):
    """Show available plans"""
    text = get_text("plans_list")
    
    for plan in PLANS:
        text += get_text("plan_details", **plan) + "\n"
    
    await message.answer(
        text,
        reply_markup=plans_keyboard(PLANS)
    )


@router.callback_query(F.data.startswith("plan:"))
async def select_plan(callback: CallbackQuery, state: FSMContext):
    """Handle plan selection"""
    plan_id = callback.data.split(":")[1]
    plan = next((p for p in PLANS if p["id"] == plan_id), None)
    
    if not plan:
        await callback.answer("پلن یافت نشد!", show_alert=True)
        return
    
    await state.update_data(
        days=plan["days"],
        traffic=plan["traffic"],
        price=plan["price"]
    )
    
    await callback.message.edit_text(
        get_text("custom_plan_confirm", **plan),
        reply_markup=confirm_order_keyboard()
    )
    await callback.answer()


@router.message(F.text == "🎨 پلن سفارشی")
async def custom_plan_start(message: Message, state: FSMContext):
    """Start custom plan flow"""
    await state.set_state(CustomPlanStates.waiting_for_days)
    await message.answer(
        get_text("custom_plan_start"),
        reply_markup=cancel_keyboard()
    )


@router.message(CustomPlanStates.waiting_for_days)
async def custom_plan_days(message: Message, state: FSMContext):
    """Handle custom plan days input"""
    if message.text == "❌ لغو":
        await state.clear()
        await message.answer("لغو شد.", reply_markup=main_menu_keyboard())
        return
    
    try:
        days = int(message.text)
        if not 1 <= days <= 365:
            await message.answer(get_text("error_out_of_range"))
            return
        
        await state.update_data(days=days)
        await state.set_state(CustomPlanStates.waiting_for_traffic)
        await message.answer(get_text("custom_plan_traffic"))
    except ValueError:
        await message.answer(get_text("error_invalid_number"))


@router.message(CustomPlanStates.waiting_for_traffic)
async def custom_plan_traffic(message: Message, state: FSMContext):
    """Handle custom plan traffic input"""
    if message.text == "❌ لغو":
        await state.clear()
        await message.answer("لغو شد.", reply_markup=main_menu_keyboard())
        return
    
    try:
        traffic = int(message.text)
        if not 1 <= traffic <= 500:
            await message.answer(get_text("error_out_of_range"))
            return
        
        data = await state.get_data()
        days = data["days"]
        price = calculate_custom_price(days, traffic)
        
        await state.update_data(traffic=traffic, price=price)
        
        await message.answer(
            get_text("custom_plan_confirm", days=days, traffic=traffic, price=price),
            reply_markup=confirm_order_keyboard()
        )
        await state.set_state(CustomPlanStates.waiting_for_confirm)
    except ValueError:
        await message.answer(get_text("error_invalid_number"))


@router.callback_query(F.data == "confirm_order")
async def confirm_order(callback: CallbackQuery, state: FSMContext):
    """Confirm and create order"""
    data = await state.get_data()
    
    async with AsyncSessionLocal() as session:
        # Create order
        order = Order(
            user_id=callback.from_user.id,
            days=data["days"],
            traffic_gb=data["traffic"],
            price=data["price"],
            status="pending"
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)
        
        # Send payment instructions
        await callback.message.edit_text(
            get_text(
                "order_created",
                order_id=order.id,
                price=order.price,
                card_number=settings.CARD_NUMBER,
                card_holder=settings.CARD_HOLDER
            )
        )
        
        # Set state to wait for receipt
        await state.update_data(order_id=order.id)
        await state.set_state(PaymentStates.waiting_for_receipt)
    
    await callback.answer()


@router.callback_query(F.data == "cancel_order")
async def cancel_order(callback: CallbackQuery, state: FSMContext):
    """Cancel order"""
    await state.clear()
    await callback.message.delete()
    await callback.bot.send_message(
        chat_id=callback.from_user.id,
        get_text("start"),
        reply_markup=main_menu_keyboard()
    )
    await callback.answer()


@router.message(PaymentStates.waiting_for_receipt, F.photo)
async def receive_receipt(message: Message, state: FSMContext):
    """Receive payment receipt"""
    data = await state.get_data()
    order_id = data.get("order_id")
    
    if not order_id:
        await message.answer(get_text("error_general"))
        return
    
    photo = message.photo[-1]
    
    async with AsyncSessionLocal() as session:
        # Update order status
        result = await session.execute(select(Order).where(Order.id == order_id))
        order = result.scalar_one_or_none()
        
        if order:
            order.status = "paid"
            
            # Create payment record
            payment = Payment(
                order_id=order_id,
                user_id=message.from_user.id,
                receipt_file_id=photo.file_id,
                status="pending"
            )
            session.add(payment)
            await session.commit()
            await session.refresh(payment)
            
            # Notify user
            await message.answer(
                get_text("receipt_received"),
                reply_markup=main_menu_keyboard()
            )
            
            # Notify admins
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
                except Exception:
                    pass
    
    await state.clear()


@router.message(F.text == "📋 سفارش‌های من")
async def my_orders(message: Message):
    """Show user orders"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Order)
            .where(Order.user_id == message.from_user.id)
            .order_by(Order.created_at.desc())
        )
        orders = result.scalars().all()
        
        if not orders:
            await message.answer(get_text("no_orders"))
            return
        
        text = get_text("my_orders") + "\n\n"
        
        status_map = {
            "pending": "در انتظار پرداخت",
            "paid": "در انتظار تایید",
            "approved": "تایید شده",
            "rejected": "رد شده",
            "completed": "تکمیل شده"
        }
        
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
        
        await message.answer(text)
