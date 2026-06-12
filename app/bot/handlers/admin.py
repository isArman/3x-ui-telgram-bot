from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from app.bot.keyboards.admin import (
    admin_menu_keyboard,
    configs_menu_keyboard,
    payment_review_keyboard,
    plan_select_keyboard,
)
from app.bot.states import AdminStates
from app.config.plans_loader import PLANS, get_plan
from app.config.settings import settings
from app.config.texts import get_text
from app.database.models import Order, Payment, PlanConfig, VPNAccount
from app.database.session import AsyncSessionLocal
from app.services.config_inventory import add_config, assign_config, count_available
from app.utils.logger import logger

router = Router()


def is_admin(user_id: int) -> bool:
    return user_id in settings.ADMIN_IDS


async def show_configs_menu(message: Message) -> None:
    await message.answer(
        "🗂 مدیریت کانفیگ‌های پلن\n\n"
        "کانفیگ‌ها (لینک subscription یا vless://) را به هر پلن اضافه کنید.\n"
        "پس از تایید پرداخت، یک کانفیگ آزاد به کاربر ارسال می‌شود.",
        reply_markup=configs_menu_keyboard(),
    )


async def show_admin_menu(message: Message) -> None:
    await message.answer(
        "⚙️ پنل ادمین\n\nیک گزینه را انتخاب کنید:",
        reply_markup=admin_menu_keyboard(),
    )


async def complete_payment_approval(
    session,
    payment: Payment,
    order: Order,
    admin_id: int,
    config_text: str,
    plan_config_id: int | None = None,
) -> None:
    payment.status = "approved"
    payment.reviewed_at = datetime.utcnow()
    payment.reviewed_by = admin_id
    order.status = "completed"

    vpn_account = VPNAccount(
        order_id=order.id,
        user_id=order.user_id,
        config_ref=str(plan_config_id) if plan_config_id else "manual",
        subscription_path=config_text,
        expires_at=datetime.utcnow() + timedelta(days=order.days),
        traffic_limit_gb=order.traffic_gb,
        is_active=True,
    )
    session.add(vpn_account)
    await session.commit()


async def send_config_to_user(bot, user_id: int, config_text: str) -> None:
    await bot.send_message(
        chat_id=user_id,
        text=get_text("payment_approved", subscription_url=config_text),
    )


# --- Config inventory admin ---


@router.message(Command("admin"))
@router.message(F.text == "⚙️ پنل ادمین")
async def admin_menu(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ شما دسترسی ادمین ندارید.")
        return
    await show_admin_menu(message)


@router.callback_query(F.data == "admin:menu")
async def admin_menu_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید!", show_alert=True)
        return
    await callback.message.edit_text(
        "⚙️ پنل ادمین\n\nیک گزینه را انتخاب کنید:",
        reply_markup=admin_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:configs")
async def admin_configs_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید!", show_alert=True)
        return
    await callback.message.edit_text(
        "🗂 مدیریت کانفیگ‌های پلن\n\n"
        "کانفیگ‌ها (لینک subscription یا vless://) را به هر پلن اضافه کنید.\n"
        "پس از تایید پرداخت، یک کانفیگ آزاد به کاربر ارسال می‌شود.",
        reply_markup=configs_menu_keyboard(),
    )
    await callback.answer()


@router.message(Command("configs"))
async def configs_menu(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ شما دسترسی ادمین ندارید.")
        return
    await show_configs_menu(message)


@router.callback_query(F.data == "configs:menu")
async def configs_menu_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید!", show_alert=True)
        return

    await callback.message.edit_text(
        "🗂 مدیریت کانفیگ‌های پلن\n\n"
        "کانفیگ‌ها (لینک subscription یا vless://) را به هر پلن اضافه کنید.\n"
        "پس از تایید پرداخت، یک کانفیگ آزاد به کاربر ارسال می‌شود.",
        reply_markup=configs_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "configs:stock")
async def configs_stock(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return

    async with AsyncSessionLocal() as session:
        lines = ["📦 موجودی کانفیگ هر پلن:\n"]
        for plan in PLANS:
            available = await count_available(session, plan["id"])
            total_result = await session.execute(
                select(func.count())
                .select_from(PlanConfig)
                .where(PlanConfig.plan_id == plan["id"])
            )
            total = total_result.scalar_one()
            lines.append(
                f"• {plan['name']} ({plan['id']}): {available} آزاد / {total} کل"
            )

    await callback.message.answer("\n".join(lines))
    await callback.answer()


@router.callback_query(F.data == "configs:add")
async def configs_add_start(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return

    await callback.message.answer(
        "پلن مورد نظر را انتخاب کنید:",
        reply_markup=plan_select_keyboard(PLANS, "configs:add_plan"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("configs:add_plan:"))
async def configs_add_plan(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return

    plan_id = callback.data.split(":")[2]
    plan = get_plan(plan_id)
    if not plan:
        await callback.answer("پلن یافت نشد!", show_alert=True)
        return

    await state.update_data(config_plan_id=plan_id)
    await state.set_state(AdminStates.waiting_for_config_text)
    await callback.message.answer(
        f"➕ افزودن کانفیگ برای «{plan['name']}»\n\n"
        "لینک subscription یا کانفیگ کامل (vless:// ...) را ارسال کنید:"
    )
    await callback.answer()


@router.message(AdminStates.waiting_for_config_text)
async def configs_receive_text(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    config_text = message.text.strip()
    if not config_text:
        await message.answer("❌ متن کانفیگ نمی‌تواند خالی باشد.")
        return

    data = await state.get_data()
    plan_id = data.get("config_plan_id")
    plan = get_plan(plan_id)
    if not plan:
        await message.answer("❌ پلن یافت نشد.")
        await state.clear()
        return

    async with AsyncSessionLocal() as session:
        entry = await add_config(session, plan_id, config_text, message.from_user.id)
        available = await count_available(session, plan_id)

    await message.answer(
        f"✅ کانفیگ #{entry.id} به «{plan['name']}» اضافه شد.\n"
        f"📦 موجودی آزاد: {available}"
    )
    await state.clear()


# --- Payment approval ---


@router.callback_query(F.data.startswith("approve_payment:"))
async def approve_payment(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("شما دسترسی ندارید!", show_alert=True)
        return

    payment_id = int(callback.data.split(":")[1])

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Payment).where(Payment.id == payment_id))
        payment = result.scalar_one_or_none()

        if not payment or payment.status != "pending":
            await callback.answer("پرداخت یافت نشد یا قبلاً بررسی شده!", show_alert=True)
            return

        result = await session.execute(select(Order).where(Order.id == payment.order_id))
        order = result.scalar_one_or_none()

        if not order:
            await callback.answer("سفارش یافت نشد!", show_alert=True)
            return

        if order.plan_id:
            plan = get_plan(order.plan_id)
            config_entry = await assign_config(session, order.plan_id, order.id)

            if config_entry:
                await complete_payment_approval(
                    session,
                    payment,
                    order,
                    callback.from_user.id,
                    config_entry.config_text,
                    config_entry.id,
                )

                try:
                    await send_config_to_user(
                        callback.bot, order.user_id, config_entry.config_text
                    )
                except Exception as exc:
                    logger.error(f"Failed to notify user {order.user_id}: {exc}")

                plan_name = plan["name"] if plan else order.plan_id
                await callback.message.answer(
                    "✅ پرداخت تایید شد و کانفیگ از موجودی ارسال شد!\n\n"
                    f"👤 کاربر: {order.user_id}\n"
                    f"📦 پلن: {plan_name}\n"
                    f"🆔 کانفیگ #{config_entry.id}\n"
                    f"🔗 {config_entry.config_text[:80]}..."
                )

                try:
                    await callback.message.edit_caption(
                        caption=(callback.message.caption or "") + "\n\n✅ تایید + ارسال کانفیگ"
                    )
                except Exception:
                    pass

                await callback.answer("کانفیگ ارسال شد!")
                logger.info(f"Assigned config {config_entry.id} to order {order.id}")
                return

            plan_name = plan["name"] if plan else order.plan_id
            await callback.answer("موجودی کانفیگ تمام شده!", show_alert=True)
            await callback.message.answer(
                f"⚠️ برای پلن «{plan_name}» کانفیگ آزاد وجود ندارد.\n"
                f"از /configs کانفیگ اضافه کنید یا لینک را دستی ارسال کنید."
            )

        await state.update_data(payment_id=payment_id, order_id=order.id)
        await state.set_state(AdminStates.waiting_for_subscription)

        plan_note = ""
        if order.plan_id:
            plan = get_plan(order.plan_id)
            plan_note = f"\n📦 پلن: {plan['name'] if plan else order.plan_id} (بدون موجودی)"

        await callback.message.answer(
            f"✅ پرداخت تایید شد!{plan_note}\n\n"
            f"👤 کاربر: {order.user_id}\n"
            f"⏱ {order.days} روز | 📊 {order.traffic_gb} GB\n\n"
            "لطفاً لینک/کانفیگ را ارسال کنید:"
        )
        await callback.answer()


@router.message(AdminStates.waiting_for_subscription)
async def receive_subscription(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    config_text = message.text.strip()
    data = await state.get_data()
    payment_id = data.get("payment_id")
    order_id = data.get("order_id")

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Payment).where(Payment.id == payment_id))
        payment = result.scalar_one_or_none()
        result = await session.execute(select(Order).where(Order.id == order_id))
        order = result.scalar_one_or_none()

        if not payment or not order:
            await message.answer("❌ خطا: سفارش یا پرداخت یافت نشد!")
            await state.clear()
            return

        await complete_payment_approval(
            session, payment, order, message.from_user.id, config_text
        )

        try:
            await send_config_to_user(message.bot, order.user_id, config_text)
            await message.answer(
                f"✅ کانفیگ ارسال شد!\n👤 کاربر: {order.user_id}\n🔗 {config_text}"
            )
        except Exception as exc:
            logger.error(f"Failed to notify user: {exc}")
            await message.answer(f"⚠️ خطا در ارسال به کاربر: {exc}")

    await state.clear()


@router.callback_query(F.data.startswith("reject_payment:"))
async def reject_payment(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("شما دسترسی ندارید!", show_alert=True)
        return

    payment_id = int(callback.data.split(":")[1])

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Payment).where(Payment.id == payment_id))
        payment = result.scalar_one_or_none()

        if not payment or payment.status != "pending":
            await callback.answer("پرداخت یافت نشد!", show_alert=True)
            return

        result = await session.execute(select(Order).where(Order.id == payment.order_id))
        order = result.scalar_one_or_none()

        if not order:
            await callback.answer("سفارش یافت نشد!", show_alert=True)
            return

        payment.status = "rejected"
        payment.reviewed_at = datetime.utcnow()
        payment.reviewed_by = callback.from_user.id
        payment.admin_note = "رد شده توسط ادمین"
        order.status = "rejected"
        await session.commit()

        try:
            await callback.bot.send_message(
                chat_id=order.user_id,
                text=get_text("payment_rejected", reason="رسید پرداخت معتبر نیست"),
            )
        except Exception as exc:
            logger.error(f"Failed to notify user: {exc}")

        try:
            await callback.message.edit_caption(
                caption=(callback.message.caption or "") + "\n\n❌ رد شد"
            )
        except Exception:
            pass

        await callback.answer("پرداخت رد شد!", show_alert=True)


@router.callback_query(F.data == "admin:dashboard")
async def admin_dashboard_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید!", show_alert=True)
        return
    await show_dashboard(callback.message)
    await callback.answer()


@router.callback_query(F.data == "admin:pending")
async def admin_pending_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید!", show_alert=True)
        return
    await show_pending_payments(callback.message)
    await callback.answer()


@router.message(Command("dashboard"))
async def show_dashboard(message: Message):
    from app.utils.statistics import get_dashboard_stats

    if not is_admin(message.from_user.id):
        await message.answer("⛔ شما دسترسی ادمین ندارید.")
        return

    try:
        async with AsyncSessionLocal() as session:
            stats = await get_dashboard_stats(session)
            stock_lines = []
            for plan in PLANS:
                n = await count_available(session, plan["id"])
                stock_lines.append(f"  • {plan['name']}: {n} آزاد")

            await message.answer(
                "📊 داشبورد مدیریت\n\n"
                f"👥 کاربران: {stats['total_users']}\n"
                f"📦 سفارشات: {stats['total_orders']}\n"
                f"⏳ پرداخت در انتظار: {stats['pending_payments']}\n"
                f"💳 اکانت فعال: {stats['active_accounts']}\n"
                f"💰 درآمد کل: {stats['total_revenue']:,} تومان\n\n"
                "📦 موجودی کانفیگ:\n" + "\n".join(stock_lines) + "\n\n"
                "🗂 از منو «⚙️ پنل ادمین» کانفیگ‌ها را مدیریت کنید."
            )
    except Exception as exc:
        logger.error(f"Error showing dashboard: {exc}")
        await message.answer("خطا در نمایش داشبورد!")


@router.message(Command("pending"))
async def show_pending_payments(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ شما دسترسی ادمین ندارید.")
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Payment)
            .where(Payment.status == "pending")
            .order_by(Payment.created_at.desc())
            .limit(10)
        )
        payments = result.scalars().all()

        if not payments:
            await message.answer("هیچ پرداخت در انتظاری وجود ندارد.")
            return

        text = "📋 پرداخت‌های در انتظار:\n\n"
        for payment in payments:
            order_result = await session.execute(
                select(Order).where(Order.id == payment.order_id)
            )
            order = order_result.scalar_one_or_none()
            if order:
                plan_label = order.plan_id or "سفارشی"
                text += (
                    f"🆔 #{payment.id} | 👤 {payment.user_id}\n"
                    f"📦 {plan_label} | {order.days}روز {order.traffic_gb}GB\n"
                    f"💰 {order.price:,} تومان\n{'─' * 25}\n"
                )

        await message.answer(text)


@router.message(Command("payments"))
async def show_payment_history(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ شما دسترسی ادمین ندارید.")
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Payment).order_by(Payment.created_at.desc()).limit(20)
        )
        payments = result.scalars().all()

        if not payments:
            await message.answer("هیچ پرداختی یافت نشد.")
            return

        status_map = {"pending": "⏳", "approved": "✅", "rejected": "❌"}
        text = "📜 تاریخچه پرداخت‌ها:\n\n"
        for payment in payments:
            order_result = await session.execute(
                select(Order).where(Order.id == payment.order_id)
            )
            order = order_result.scalar_one_or_none()
            if order:
                icon = status_map.get(payment.status, "?")
                text += f"{icon} #{payment.id} | {order.price:,}T | {payment.created_at:%Y-%m-%d}\n"

        await message.answer(text)
