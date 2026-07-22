from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.auth import deny_non_admin_callback, is_admin
from app.bot.constants import (
    ADMIN_MENU_TEXT,
    BTN_ADMIN_PANEL,
    CONFIGS_MENU_TEXT,
    MAIN_MENU_BUTTONS,
)
from app.bot.menu_dispatch import dispatch_main_menu
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
from app.database.models import Order, Payment, PlanConfig, User, VPNAccount
from app.database.session import AsyncSessionLocal
from app.services.config_inventory import add_config, assign_config, count_available
from app.services.panel_settings import (
    PROVISIONING_AUTO,
    get_panel_settings,
    is_auto_provisioning_ready,
)
from app.services.renewal import extend_vpn_account
from app.services.xui_provisioning import provision_subscription_for_order
from app.utils.logger import logger
from app.utils.validation import is_valid_config_text

router = Router()


async def show_configs_menu(message: Message) -> None:
    await message.answer(CONFIGS_MENU_TEXT, reply_markup=configs_menu_keyboard())


async def show_admin_menu(message: Message) -> None:
    await message.answer(ADMIN_MENU_TEXT, reply_markup=admin_menu_keyboard())


async def complete_payment_approval(
    session,
    payment: Payment,
    order: Order,
    admin_id: int,
    config_text: str,
    plan_config_id: int | None = None,
    config_ref: str | None = None,
) -> None:
    payment.status = "approved"
    payment.reviewed_at = datetime.utcnow()
    payment.reviewed_by = admin_id
    order.status = "completed"

    vpn_account = VPNAccount(
        order_id=order.id,
        user_id=order.user_id,
        config_ref=config_ref or (str(plan_config_id) if plan_config_id else "manual"),
        subscription_path=config_text,
        expires_at=datetime.utcnow() + timedelta(days=order.days),
        traffic_limit_gb=order.traffic_gb,
        is_active=True,
    )
    session.add(vpn_account)
    await session.commit()


async def complete_renewal_approval(
    session,
    payment: Payment,
    order: Order,
    admin_id: int,
    vpn_account: VPNAccount,
    subscription_url: str | None = None,
) -> VPNAccount:
    payment.status = "approved"
    payment.reviewed_at = datetime.utcnow()
    payment.reviewed_by = admin_id
    order.status = "completed"
    await extend_vpn_account(session, vpn_account, order, subscription_url)
    await session.commit()
    await session.refresh(vpn_account)
    return vpn_account


async def send_renewal_to_user(bot, user_id: int, vpn_account: VPNAccount, order: Order) -> None:
    await bot.send_message(
        chat_id=user_id,
        text=get_text(
            "renewal_approved",
            days=order.days,
            traffic=order.traffic_gb,
            expires_at=vpn_account.expires_at.strftime("%Y-%m-%d"),
            subscription_url=vpn_account.subscription_path,
        ),
    )


async def send_config_to_user(bot, user_id: int, config_text: str) -> None:
    await bot.send_message(
        chat_id=user_id,
        text=get_text("payment_approved", subscription_url=config_text),
    )


async def _finalize_config_delivery(
    callback: CallbackQuery,
    order: Order,
    payment: Payment,
    config_text: str,
    source_label: str,
    plan_name: str | None = None,
) -> None:
    try:
        await send_config_to_user(callback.bot, order.user_id, config_text)
    except Exception as exc:
        logger.error(f"Failed to notify user {order.user_id}: {exc}")

    label = plan_name or order.plan_id or "سفارشی"
    preview = config_text if len(config_text) <= 120 else config_text[:120] + "..."
    await callback.message.answer(
        f"✅ پرداخت تایید شد ({source_label})!\n\n"
        f"👤 کاربر: {order.user_id}\n"
        f"📦 پلن: {label}\n"
        f"🔗 {preview}"
    )
    try:
        await callback.message.edit_caption(
            caption=(callback.message.caption or "") + f"\n\n✅ تایید ({source_label})"
        )
    except Exception:
        pass
    await callback.answer("کانفیگ ارسال شد!")


async def _finalize_renewal_delivery(
    callback: CallbackQuery,
    order: Order,
    payment: Payment,
    vpn_account: VPNAccount,
    source_label: str,
    plan_name: str | None = None,
) -> None:
    try:
        await send_renewal_to_user(callback.bot, order.user_id, vpn_account, order)
    except Exception as exc:
        logger.error("Failed to notify user %s about renewal: %s", order.user_id, exc)

    label = plan_name or order.plan_id or "سفارشی"
    await callback.message.answer(
        f"✅ تمدید تایید شد ({source_label})!\n\n"
        f"👤 کاربر: {order.user_id}\n"
        f"📦 پلن: {label}\n"
        f"🆔 اکانت سفارش #{vpn_account.order_id}\n"
        f"📅 انقضای جدید: {vpn_account.expires_at:%Y-%m-%d}"
    )
    try:
        await callback.message.edit_caption(
            caption=(callback.message.caption or "") + f"\n\n✅ تمدید ({source_label})"
        )
    except Exception:
        pass
    await callback.answer("تمدید انجام شد!")


async def _try_auto_provision(
    session,
    order: Order,
    user: User,
    vpn_account: VPNAccount | None = None,
) -> str | None:
    panel = await get_panel_settings(session)
    if not is_auto_provisioning_ready(panel):
        return None

    return await provision_subscription_for_order(
        session, panel, user, order, existing_account=vpn_account
    )


async def _try_inventory_fallback(
    session,
    order: Order,
) -> PlanConfig | None:
    if not order.plan_id:
        return None
    return await assign_config(session, order.plan_id, order.id)


# --- Config inventory admin ---


@router.message(
    StateFilter(AdminStates.waiting_for_config_text),
    F.text.in_(MAIN_MENU_BUTTONS),
)
async def admin_fsm_menu_interrupt(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await dispatch_main_menu(message, state)


@router.message(
    StateFilter(AdminStates.waiting_for_subscription),
    F.text.in_(MAIN_MENU_BUTTONS),
)
async def admin_subscription_menu_interrupt(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "⚠️ در حال انتظار ارسال لینک subscription هستید.\n"
        "لطفاً لینک را ارسال کنید یا از دکمه «❌ لغو» برای انصراف استفاده کنید."
    )


@router.message(Command("admin"))
@router.message(F.text == BTN_ADMIN_PANEL)
async def admin_menu(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ شما دسترسی ادمین ندارید.")
        return
    await state.clear()
    await show_admin_menu(message)


@router.callback_query(F.data == "admin:menu")
async def admin_menu_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید!", show_alert=True)
        return
    await callback.message.edit_text(
        ADMIN_MENU_TEXT,
        reply_markup=admin_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:configs")
async def admin_configs_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید!", show_alert=True)
        return
    await callback.message.edit_text(
        CONFIGS_MENU_TEXT,
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
        CONFIGS_MENU_TEXT,
        reply_markup=configs_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "configs:stock")
async def configs_stock(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید!", show_alert=True)
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
        await callback.answer("دسترسی ندارید!", show_alert=True)
        return

    await callback.message.answer(
        "پلن مورد نظر را انتخاب کنید:",
        reply_markup=plan_select_keyboard(PLANS, "configs:add_plan"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("configs:add_plan:"))
async def configs_add_plan(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید!", show_alert=True)
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

    if not message.text or not message.text.strip():
        await message.answer("❌ لطفاً لینک/متن کانفیگ را به صورت متن ارسال کنید.")
        return

    config_text = message.text.strip()
    if not is_valid_config_text(config_text):
        await message.answer(
            "❌ فرمت کانفیگ نامعتبر است. لینک http(s):// یا vless:// و مشابه بفرستید."
        )
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

        plan = get_plan(order.plan_id) if order.plan_id else None
        plan_name = plan["name"] if plan else None

        user_result = await session.execute(select(User).where(User.id == order.user_id))
        user = user_result.scalar_one_or_none()
        if not user:
            await callback.answer("کاربر یافت نشد!", show_alert=True)
            return

        # --- Renewal order ---
        if order.renew_vpn_account_id:
            acc_result = await session.execute(
                select(VPNAccount).where(VPNAccount.id == order.renew_vpn_account_id)
            )
            vpn_account = acc_result.scalar_one_or_none()
            if not vpn_account or vpn_account.user_id != order.user_id:
                await callback.answer("اکانت تمدید یافت نشد!", show_alert=True)
                return

            sub_url = await _try_auto_provision(session, order, user, vpn_account)
            if sub_url:
                vpn_account = await complete_renewal_approval(
                    session,
                    payment,
                    order,
                    callback.from_user.id,
                    vpn_account,
                    sub_url,
                )
                logger.info(
                    "Auto-renewed account %s for order %s", vpn_account.id, order.id
                )
                await _finalize_renewal_delivery(
                    callback, order, payment, vpn_account, "خودکار 3x-ui", plan_name
                )
                return

            await complete_renewal_approval(
                session,
                payment,
                order,
                callback.from_user.id,
                vpn_account,
                None,
            )
            logger.info("Renewed account %s (DB only) for order %s", vpn_account.id, order.id)
            await _finalize_renewal_delivery(
                callback, order, payment, vpn_account, "تمدید دستی", plan_name
            )
            return

        # 1) Auto provisioning via 3x-ui (if enabled)
        sub_url = await _try_auto_provision(session, order, user)
        if sub_url:
            await complete_payment_approval(
                session,
                payment,
                order,
                callback.from_user.id,
                sub_url,
                config_ref="xui-auto",
            )
            logger.info("Auto-provisioned order %s for user %s", order.id, order.user_id)
            await _finalize_config_delivery(
                callback, order, payment, sub_url, "خودکار 3x-ui", plan_name
            )
            return

        # 2) Fallback: manual inventory (pre-stocked configs)
        config_entry = await _try_inventory_fallback(session, order)
        if config_entry:
            await complete_payment_approval(
                session,
                payment,
                order,
                callback.from_user.id,
                config_entry.config_text,
                config_entry.id,
            )
            logger.info(f"Assigned config {config_entry.id} to order {order.id}")
            await _finalize_config_delivery(
                callback,
                order,
                payment,
                config_entry.config_text,
                "انبار دستی",
                plan_name,
            )
            return

        if order.plan_id and plan_name:
            await callback.message.answer(
                f"⚠️ ساخت خودکار ناموفق و موجودی پلن «{plan_name}» خالی است.\n"
                f"لینک را دستی ارسال کنید یا از /configs کانفیگ اضافه کنید."
            )
        elif (await get_panel_settings(session)).provisioning_mode == PROVISIONING_AUTO:
            await callback.message.answer(
                "⚠️ ساخت خودکار در 3x-ui ناموفق بود.\n"
                "لینک subscription را دستی ارسال کنید."
            )

        await state.update_data(payment_id=payment_id, order_id=order.id)
        await state.set_state(AdminStates.waiting_for_subscription)

        plan_note = ""
        if order.plan_id:
            plan_note = f"\n📦 پلن: {plan_name or order.plan_id}"

        await callback.message.answer(
            f"⏳ پرداخت در انتظار ارسال لینک{plan_note}\n\n"
            f"👤 کاربر: {order.user_id}\n"
            f"⏱ {order.days} روز | 📊 {order.traffic_gb} GB\n\n"
            "لطفاً لینک subscription را ارسال کنید:\n"
            "(یا «❌ لغو» برای انصراف)"
        )
        await callback.answer()


@router.message(AdminStates.waiting_for_subscription)
async def receive_subscription(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    if message.text == "❌ لغو":
        await state.clear()
        await message.answer(
            "ارسال لینک لغو شد. پرداخت همچنان در انتظار است.",
            reply_markup=admin_menu_keyboard(),
        )
        return

    if not message.text or not message.text.strip():
        await message.answer("❌ لطفاً لینک/کانفیگ را به صورت متن ارسال کنید.")
        return

    config_text = message.text.strip()
    if not is_valid_config_text(config_text):
        await message.answer(
            "❌ فرمت کانفیگ نامعتبر است. لینک http(s):// یا vless:// و مشابه بفرستید."
        )
        return

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

        if payment.status != "pending":
            await message.answer("❌ این پرداخت قبلاً بررسی شده است.")
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


async def _send_dashboard(bot, chat_id: int) -> None:
    from app.utils.statistics import get_dashboard_stats

    async with AsyncSessionLocal() as session:
        stats = await get_dashboard_stats(session)
        stock_lines = []
        for plan in PLANS:
            n = await count_available(session, plan["id"])
            stock_lines.append(f"  • {plan['name']}: {n} آزاد")

        await bot.send_message(
            chat_id=chat_id,
            text=(
                "📊 داشبورد مدیریت\n\n"
                f"👥 کاربران: {stats['total_users']}\n"
                f"📦 سفارشات: {stats['total_orders']}\n"
                f"⏳ پرداخت در انتظار: {stats['pending_payments']}\n"
                f"💳 اکانت فعال: {stats['active_accounts']}\n"
                f"💰 درآمد کل: {stats['total_revenue']:,} تومان\n\n"
                "📦 موجودی کانفیگ:\n" + "\n".join(stock_lines) + "\n\n"
                "🗂 از منو «⚙️ پنل ادمین» کانفیگ‌ها را مدیریت کنید."
            ),
        )


async def _send_pending_payments(bot, chat_id: int) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Payment)
            .where(Payment.status == "pending")
            .order_by(Payment.created_at.desc())
            .limit(10)
        )
        payments = result.scalars().all()

        if not payments:
            await bot.send_message(chat_id=chat_id, text="هیچ پرداخت در انتظاری وجود ندارد.")
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

        await bot.send_message(chat_id=chat_id, text=text)


@router.callback_query(F.data == "admin:dashboard")
async def admin_dashboard_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید!", show_alert=True)
        return
    try:
        await _send_dashboard(callback.bot, callback.message.chat.id)
    except Exception as exc:
        logger.error(f"Error showing dashboard: {exc}")
        await callback.message.answer("خطا در نمایش داشبورد!")
    await callback.answer()


@router.callback_query(F.data == "admin:pending")
async def admin_pending_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید!", show_alert=True)
        return
    await _send_pending_payments(callback.bot, callback.message.chat.id)
    await callback.answer()


@router.message(Command("dashboard"))
async def show_dashboard(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ شما دسترسی ادمین ندارید.")
        return
    try:
        await _send_dashboard(message.bot, message.chat.id)
    except Exception as exc:
        logger.error(f"Error showing dashboard: {exc}")
        await message.answer("خطا در نمایش داشبورد!")


@router.message(Command("pending"))
async def show_pending_payments(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ شما دسترسی ادمین ندارید.")
        return
    await _send_pending_payments(message.bot, message.chat.id)


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
