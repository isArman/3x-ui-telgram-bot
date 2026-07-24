from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from app.bot.auth import is_admin
from app.bot.constants import (
    ADMIN_MENU_TEXT,
    BTN_ADMIN_PANEL,
    CONFIGS_MENU_TEXT,
    MAIN_MENU_BUTTONS,
)
from app.bot.menu_dispatch import dispatch_main_menu
from sqlalchemy import func, select

from app.bot.keyboards.admin import (
    admin_cancel_keyboard,
    admin_menu_keyboard,
    configs_menu_keyboard,
    plan_select_keyboard,
)
from app.bot.states import AdminStates
from app.config.plans_loader import PLANS, get_plan
from app.config.texts import get_text
from app.database.models import Order, Payment, PlanConfig, VPNAccount, WalletTopUp
from app.database.session import AsyncSessionLocal
from app.services.config_inventory import add_config, count_available
from app.services.order_fulfillment import (
    complete_payment_approval,
    fulfill_paid_order,
)
from app.services.panel_settings import PROVISIONING_AUTO, get_panel_settings
from app.services.wallet import credit_balance
from app.utils.logger import logger
from app.utils.validation import is_valid_config_text

router = Router()


async def show_configs_menu(message: Message) -> None:
    await message.answer(CONFIGS_MENU_TEXT, reply_markup=configs_menu_keyboard())


async def show_admin_menu(message: Message) -> None:
    await message.answer(ADMIN_MENU_TEXT, reply_markup=admin_menu_keyboard())


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


async def refund_order_wallet_debit(session, order: Order) -> int | None:
    """Refund wallet_debit to user if any. Returns new balance or None."""
    debit = int(order.wallet_debit or 0)
    if debit <= 0:
        return None
    new_balance = await credit_balance(session, order.user_id, debit)
    order.wallet_debit = 0
    return new_balance



# --- Config inventory admin ---


@router.message(
    StateFilter(AdminStates.waiting_for_config_text),
    F.text.in_(MAIN_MENU_BUTTONS),
)
@router.message(
    StateFilter(AdminStates.waiting_for_topup_amount),
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

        try:
            fulfill = await fulfill_paid_order(
                session, payment, order, callback.from_user.id, plan_name
            )
        except ValueError as exc:
            await callback.answer(str(exc), show_alert=True)
            return

        if fulfill.referral_cashback:
            from app.bot.handlers.user import notify_referral_cashback

            await notify_referral_cashback(callback.bot, fulfill.referral_cashback)

        if fulfill.kind == "renewal_auto":
            logger.info(
                "Auto-renewed account %s for order %s", fulfill.vpn_account.id, order.id
            )
            await _finalize_renewal_delivery(
                callback, order, payment, fulfill.vpn_account, "خودکار 3x-ui", plan_name
            )
            return

        if fulfill.kind == "renewal_db":
            logger.info(
                "Renewed account %s (DB only) for order %s",
                fulfill.vpn_account.id,
                order.id,
            )
            await _finalize_renewal_delivery(
                callback, order, payment, fulfill.vpn_account, "تمدید دستی", plan_name
            )
            return

        if fulfill.kind == "new_auto":
            logger.info("Auto-provisioned order %s for user %s", order.id, order.user_id)
            await _finalize_config_delivery(
                callback, order, payment, fulfill.config_text, "خودکار 3x-ui", plan_name
            )
            return

        if fulfill.kind == "new_inventory":
            logger.info("Assigned config %s to order %s", fulfill.plan_config_id, order.id)
            await _finalize_config_delivery(
                callback,
                order,
                payment,
                fulfill.config_text,
                "انبار دستی",
                plan_name,
            )
            return

        # needs_manual
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
            "لطفاً لینک subscription را ارسال کنید:",
            reply_markup=admin_cancel_keyboard(),
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
            reply_markup=ReplyKeyboardRemove(),
        )
        await show_admin_menu(message)
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

        cashback = await complete_payment_approval(
            session, payment, order, message.from_user.id, config_text
        )

        from app.bot.handlers.user import notify_referral_cashback

        await notify_referral_cashback(message.bot, cashback)

        try:
            await send_config_to_user(message.bot, order.user_id, config_text)
            await message.answer(
                f"✅ کانفیگ ارسال شد!\n👤 کاربر: {order.user_id}\n🔗 {config_text}",
                reply_markup=ReplyKeyboardRemove(),
            )
            await show_admin_menu(message)
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
        new_balance = await refund_order_wallet_debit(session, order)
        await session.commit()

        try:
            await callback.bot.send_message(
                chat_id=order.user_id,
                text=get_text("payment_rejected", reason="رسید پرداخت معتبر نیست"),
            )
            if new_balance is not None:
                await callback.bot.send_message(
                    chat_id=order.user_id,
                    text=get_text("wallet_refund_on_reject", balance=new_balance),
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


# --- Wallet top-up review ---


async def _credit_topup(
    session,
    topup: WalletTopUp,
    credited_amount: int,
    admin_id: int,
) -> int:
    topup.credited_amount = credited_amount
    topup.status = "approved"
    topup.reviewed_at = datetime.utcnow()
    topup.reviewed_by = admin_id
    return await credit_balance(session, topup.user_id, credited_amount)


@router.callback_query(F.data.startswith("approve_topup:"))
async def approve_topup(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("شما دسترسی ندارید!", show_alert=True)
        return

    topup_id = int(callback.data.split(":")[1])

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(WalletTopUp).where(WalletTopUp.id == topup_id)
        )
        topup = result.scalar_one_or_none()

        if not topup or topup.status != "pending":
            await callback.answer("درخواست یافت نشد یا قبلاً بررسی شده!", show_alert=True)
            return

        if not topup.receipt_file_id:
            await callback.answer("هنوز رسیدی دریافت نشده است!", show_alert=True)
            return

        amount = topup.requested_amount
        balance = await _credit_topup(session, topup, amount, callback.from_user.id)
        await session.commit()

        try:
            await callback.bot.send_message(
                chat_id=topup.user_id,
                text=get_text("wallet_topup_approved", amount=amount, balance=balance),
            )
        except Exception as exc:
            logger.error("Failed to notify user about topup %s: %s", topup_id, exc)

        try:
            await callback.message.edit_caption(
                caption=(callback.message.caption or "")
                + f"\n\n✅ تایید مبلغ درخواستی ({amount:,})"
            )
        except Exception:
            pass

        await callback.message.answer(
            get_text("admin_topup_credited_requested", amount=amount)
        )
        await callback.answer("شارژ تایید شد!")


@router.callback_query(F.data.startswith("manual_topup:"))
async def manual_topup_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("شما دسترسی ندارید!", show_alert=True)
        return

    topup_id = int(callback.data.split(":")[1])

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(WalletTopUp).where(WalletTopUp.id == topup_id)
        )
        topup = result.scalar_one_or_none()

        if not topup or topup.status != "pending":
            await callback.answer("درخواست یافت نشد یا قبلاً بررسی شده!", show_alert=True)
            return

    await state.update_data(manual_topup_id=topup_id)
    await state.set_state(AdminStates.waiting_for_topup_amount)
    await callback.message.answer(
        get_text(
            "admin_topup_ask_manual",
            requested_amount=topup.requested_amount,
        ),
        reply_markup=admin_cancel_keyboard(),
    )
    await callback.answer()


@router.message(AdminStates.waiting_for_topup_amount)
async def manual_topup_amount(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    if message.text == "❌ لغو":
        await state.clear()
        await message.answer(
            "ثبت مبلغ دستی لغو شد.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await show_admin_menu(message)
        return

    if await dispatch_main_menu(message, state):
        return

    try:
        amount = int(message.text.replace(",", "").replace("،", "").strip())
        if amount < 1:
            await message.answer(get_text("error_invalid_number"))
            return
    except (TypeError, ValueError):
        await message.answer(get_text("error_invalid_number"))
        return

    data = await state.get_data()
    topup_id = data.get("manual_topup_id")
    if not topup_id:
        await message.answer(get_text("error_general"))
        await state.clear()
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(WalletTopUp).where(WalletTopUp.id == topup_id)
        )
        topup = result.scalar_one_or_none()

        if not topup or topup.status != "pending":
            await message.answer("درخواست یافت نشد یا قبلاً بررسی شده!")
            await state.clear()
            return

        requested = topup.requested_amount
        balance = await _credit_topup(session, topup, amount, message.from_user.id)
        await session.commit()

        try:
            if amount == requested:
                user_text = get_text(
                    "wallet_topup_approved", amount=amount, balance=balance
                )
            else:
                user_text = get_text(
                    "wallet_topup_approved_manual",
                    requested_amount=requested,
                    credited_amount=amount,
                    balance=balance,
                )
            await message.bot.send_message(chat_id=topup.user_id, text=user_text)
        except Exception as exc:
            logger.error("Failed to notify user about topup %s: %s", topup_id, exc)

        await message.answer(
            get_text(
                "admin_topup_credited",
                credited_amount=amount,
                requested_amount=requested,
            ),
            reply_markup=ReplyKeyboardRemove(),
        )
        await show_admin_menu(message)

    await state.clear()


@router.callback_query(F.data.startswith("reject_topup:"))
async def reject_topup(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("شما دسترسی ندارید!", show_alert=True)
        return

    topup_id = int(callback.data.split(":")[1])

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(WalletTopUp).where(WalletTopUp.id == topup_id)
        )
        topup = result.scalar_one_or_none()

        if not topup or topup.status != "pending":
            await callback.answer("درخواست یافت نشد!", show_alert=True)
            return

        topup.status = "rejected"
        topup.reviewed_at = datetime.utcnow()
        topup.reviewed_by = callback.from_user.id
        topup.admin_note = "رد شده توسط ادمین"
        await session.commit()

        try:
            await callback.bot.send_message(
                chat_id=topup.user_id,
                text=get_text(
                    "wallet_topup_rejected",
                    reason="رسید پرداخت معتبر نیست",
                ),
            )
        except Exception as exc:
            logger.error("Failed to notify user about topup reject %s: %s", topup_id, exc)

        try:
            await callback.message.edit_caption(
                caption=(callback.message.caption or "") + "\n\n❌ رد شد"
            )
        except Exception:
            pass

        await callback.answer("درخواست شارژ رد شد!", show_alert=True)


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

        topup_result = await session.execute(
            select(WalletTopUp)
            .where(WalletTopUp.status == "pending")
            .order_by(WalletTopUp.created_at.desc())
            .limit(10)
        )
        topups = topup_result.scalars().all()

        if not payments and not topups:
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
                wallet_debit = int(order.wallet_debit or 0)
                amount_due = order.price - wallet_debit
                wallet_line = ""
                if wallet_debit > 0:
                    wallet_line = (
                        f"💳 کیف پول: {wallet_debit:,} | "
                        f"💵 رسید: {amount_due:,}\n"
                    )
                text += (
                    f"🆔 پرداخت #{payment.id} | 👤 {payment.user_id}\n"
                    f"📦 {plan_label} | {order.days}روز {order.traffic_gb}GB\n"
                    f"💰 کل: {order.price:,} تومان\n"
                    f"{wallet_line}"
                    f"{'─' * 25}\n"
                )

        if topups:
            text += "\n💳 شارژ کیف پول در انتظار:\n\n"
            for topup in topups:
                text += (
                    f"🆔 شارژ #{topup.id} | 👤 {topup.user_id}\n"
                    f"💰 درخواستی: {topup.requested_amount:,} تومان\n"
                    f"{'─' * 25}\n"
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
