from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from app.bot.auth import is_admin
from app.bot.nav import is_nav_text
from app.bot.constants import (
    ADMIN_MENU_TEXT,
    BTN_ADMIN_PANEL,
    CANCEL_BUTTON,
    CONFIGS_MENU_TEXT,
    MAIN_MENU_BUTTONS,
    button_texts,
)
from app.bot.menu_dispatch import dispatch_main_menu
from sqlalchemy import func, select

from app.bot.keyboards.admin import (
    admin_broadcast_keyboard,
    admin_cancel_keyboard,
    admin_menu_keyboard,
    admin_promo_keyboard,
    admin_promo_kind_keyboard,
    admin_promo_list_keyboard,
    admin_user_keyboard,
    configs_menu_keyboard,
    plan_select_keyboard,
)
from app.bot.states import AdminStates
from app.config.settings import settings
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
from app.services.plans_catalog import get_plan, list_active_plans, list_all_plans
from app.services.promocodes import (
    PROMO_KIND_DAYS,
    PROMO_KIND_GB,
    PROMO_KIND_PERCENT,
    create_promo,
    delete_promo,
    describe_promo,
    get_promo,
    list_promos,
    set_promo_active,
)
from app.services.users import (
    find_user,
    get_user_summary,
    set_user_blocked,
)
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
        f"پرداخت تایید شد ({source_label}).\n\n"
        f"کاربر: {order.user_id}\n"
        f"پلن: {label}\n"
        f"{preview}"
    )
    try:
        await callback.message.edit_caption(
            caption=(callback.message.caption or "") + f"\n\nتایید شد ({source_label})"
        )
    except Exception:
        pass
    await callback.answer("لینک ارسال شد.")


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
        f"تمدید تایید شد ({source_label}).\n\n"
        f"کاربر: {order.user_id}\n"
        f"پلن: {label}\n"
        f"اکانت سفارش #{vpn_account.order_id}\n"
        f"انقضای جدید: {vpn_account.expires_at:%Y-%m-%d}"
    )
    try:
        await callback.message.edit_caption(
            caption=(callback.message.caption or "") + f"\n\nتمدید شد ({source_label})"
        )
    except Exception:
        pass
    await callback.answer("تمدید انجام شد.")


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
@router.message(
    StateFilter(AdminStates.waiting_for_broadcast_text),
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
        "در حال انتظار ارسال لینک subscription هستید.\n"
        "لینک را بفرستید یا از دکمه «لغو» برای انصراف استفاده کنید."
    )


@router.message(Command("admin"))
@router.message(F.text.in_(button_texts(BTN_ADMIN_PANEL)))
async def admin_menu(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("شما دسترسی ادمین ندارید.")
        return
    await state.clear()
    await show_admin_menu(message)


@router.callback_query(F.data == "admin:menu")
async def admin_menu_callback(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        ADMIN_MENU_TEXT,
        reply_markup=admin_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:configs")
async def admin_configs_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    await callback.message.edit_text(
        CONFIGS_MENU_TEXT,
        reply_markup=configs_menu_keyboard(),
    )
    await callback.answer()


@router.message(Command("configs"))
async def configs_menu(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("شما دسترسی ادمین ندارید.")
        return
    await show_configs_menu(message)


@router.callback_query(F.data == "configs:menu")
async def configs_menu_callback(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return

    await state.clear()
    await callback.message.edit_text(
        CONFIGS_MENU_TEXT,
        reply_markup=configs_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "configs:stock")
async def configs_stock(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        plans = await list_all_plans(session)
        lines = ["موجودی کانفیگ هر پلن:\n"]
        for plan in plans:
            available = await count_available(session, plan.id)
            total_result = await session.execute(
                select(func.count())
                .select_from(PlanConfig)
                .where(PlanConfig.plan_id == plan.id)
            )
            total = total_result.scalar_one()
            status = "" if plan.is_active else " [غیرفعال]"
            lines.append(
                f"• {plan.name} ({plan.id}){status}: {available} آزاد / {total} کل"
            )

    await callback.message.answer("\n".join(lines) if len(lines) > 1 else "پلنی نیست.")
    await callback.answer()


@router.callback_query(F.data == "configs:add")
async def configs_add_start(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        plans = await list_active_plans(session)
    if not plans:
        await callback.message.answer("اول از «مدیریت پلن‌ها» یک پلن فعال بسازید.")
        await callback.answer()
        return
    await callback.message.answer(
        "پلن مورد نظر را انتخاب کنید:",
        reply_markup=plan_select_keyboard(plans, "configs:add_plan"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("configs:add_plan:"))
async def configs_add_plan(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return

    plan_id = callback.data.split(":")[2]
    async with AsyncSessionLocal() as session:
        plan = await get_plan(session, plan_id)
    if not plan:
        await callback.answer("پلن پیدا نشد.", show_alert=True)
        return

    await state.update_data(config_plan_id=plan_id)
    await state.set_state(AdminStates.waiting_for_config_text)
    await callback.message.answer(
        f"افزودن کانفیگ برای «{plan['name']}»\n\n"
        "لینک subscription یا کانفیگ کامل (vless:// ...) را بفرستید:\n"
        f"برای لغو: «{CANCEL_BUTTON}»",
        reply_markup=admin_cancel_keyboard(),
    )
    await callback.answer()


@router.message(AdminStates.waiting_for_config_text)
async def configs_receive_text(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    if is_nav_text(message.text):
        await state.clear()
        await message.answer(
            "افزودن کانفیگ لغو شد.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await show_admin_menu(message)
        return

    if not message.text or not message.text.strip():
        await message.answer("لینک یا متن کانفیگ را به صورت متن بفرستید.")
        return

    config_text = message.text.strip()
    if not is_valid_config_text(config_text):
        await message.answer(
            "فرمت کانفیگ نامعتبر است. لینک http(s):// یا vless:// بفرستید."
        )
        return

    data = await state.get_data()
    plan_id = data.get("config_plan_id")
    async with AsyncSessionLocal() as session:
        plan = await get_plan(session, plan_id)
        if not plan:
            await message.answer("پلن پیدا نشد.")
            await state.clear()
            return

        entry = await add_config(session, plan_id, config_text, message.from_user.id)
        available = await count_available(session, plan_id)

    await message.answer(
        f"کانفیگ #{entry.id} به «{plan['name']}» اضافه شد.\n"
        f"موجودی آزاد: {available}",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.clear()
    await show_admin_menu(message)


# --- Payment approval ---


@router.callback_query(F.data.startswith("approve_payment:"))
async def approve_payment(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("شما دسترسی ندارید.", show_alert=True)
        return

    try:
        payment_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("پرداخت پیدا نشد.", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Payment).where(Payment.id == payment_id))
        payment = result.scalar_one_or_none()

        if not payment or payment.status != "pending":
            await callback.answer("پرداخت پیدا نشد یا قبلاً بررسی شده.", show_alert=True)
            return

        result = await session.execute(select(Order).where(Order.id == payment.order_id))
        order = result.scalar_one_or_none()

        if not order:
            await callback.answer("سفارش پیدا نشد.", show_alert=True)
            return

        plan = await get_plan(session, order.plan_id) if order.plan_id else None
        plan_name = plan["name"] if plan else None

        try:
            fulfill = await fulfill_paid_order(
                session, payment, order, callback.from_user.id, plan_name
            )
        except ValueError as exc:
            await session.rollback()
            await callback.answer(str(exc), show_alert=True)
            return
        except Exception as exc:
            # Never leave the admin with a spinner: the payment stays pending
            # and can be reviewed again.
            logger.error("Failed to fulfill order %s: %s", order.id, exc, exc_info=True)
            await session.rollback()
            await callback.answer("خطا در فعال‌سازی سفارش.", show_alert=True)
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
                f"ساخت خودکار ناموفق بود و موجودی پلن «{plan_name}» خالی است.\n"
                f"لینک را دستی بفرستید یا از /configs کانفیگ اضافه کنید."
            )
        elif (await get_panel_settings(session)).provisioning_mode == PROVISIONING_AUTO:
            await callback.message.answer(
                "ساخت خودکار در 3x-ui ناموفق بود.\n"
                "لینک subscription را دستی بفرستید."
            )

        await state.update_data(payment_id=payment_id, order_id=order.id)
        await state.set_state(AdminStates.waiting_for_subscription)

        plan_note = ""
        if order.plan_id:
            plan_note = f"\nپلن: {plan_name or order.plan_id}"

        await callback.message.answer(
            f"پرداخت در انتظار ارسال لینک{plan_note}\n\n"
            f"کاربر: {order.user_id}\n"
            f"{order.days} روز | {order.traffic_gb} GB\n\n"
            "لینک subscription را بفرستید:",
            reply_markup=admin_cancel_keyboard(),
        )
        await callback.answer()


@router.message(AdminStates.waiting_for_subscription)
async def receive_subscription(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    if is_nav_text(message.text):
        await state.clear()
        await message.answer(
            "ارسال لینک لغو شد. پرداخت همچنان در انتظار است.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await show_admin_menu(message)
        return

    if not message.text or not message.text.strip():
        await message.answer("لینک یا کانفیگ را به صورت متن بفرستید.")
        return

    config_text = message.text.strip()
    if not is_valid_config_text(config_text):
        await message.answer(
            "فرمت کانفیگ نامعتبر است. لینک http(s):// یا vless:// بفرستید."
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
            await message.answer("خطا: سفارش یا پرداخت پیدا نشد.")
            await state.clear()
            return

        if payment.status != "pending":
            await message.answer("این پرداخت قبلاً بررسی شده است.")
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
                f"لینک ارسال شد.\nکاربر: {order.user_id}\n{config_text}",
                reply_markup=ReplyKeyboardRemove(),
            )
            await show_admin_menu(message)
        except Exception as exc:
            logger.error(f"Failed to notify user: {exc}")
            await message.answer(f"خطا در ارسال به کاربر: {exc}")

    await state.clear()


@router.callback_query(F.data.startswith("reject_payment:"))
async def reject_payment(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("شما دسترسی ندارید.", show_alert=True)
        return

    try:
        payment_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("پرداخت پیدا نشد.", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Payment).where(Payment.id == payment_id))
        payment = result.scalar_one_or_none()

        if not payment or payment.status != "pending":
            await callback.answer("پرداخت پیدا نشد.", show_alert=True)
            return

        result = await session.execute(select(Order).where(Order.id == payment.order_id))
        order = result.scalar_one_or_none()

        if not order:
            await callback.answer("سفارش پیدا نشد.", show_alert=True)
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
                caption=(callback.message.caption or "") + "\n\nرد شد"
            )
        except Exception:
            pass

        await callback.answer("پرداخت رد شد.", show_alert=True)


# --- Wallet top-up review ---


async def _credit_topup(
    session,
    topup: WalletTopUp,
    credited_amount: int,
    admin_id: int,
) -> int | None:
    """
    Atomically claim a pending top-up and credit the user.
    Returns new balance, or None if already processed by another admin.
    """
    from sqlalchemy import update

    result = await session.execute(
        update(WalletTopUp)
        .where(
            WalletTopUp.id == topup.id,
            WalletTopUp.status == "pending",
        )
        .values(
            credited_amount=credited_amount,
            status="approved",
            reviewed_at=datetime.utcnow(),
            reviewed_by=admin_id,
        )
    )
    if result.rowcount != 1:
        logger.info("Topup %s already processed; claim skipped", topup.id)
        return None

    balance = await credit_balance(session, topup.user_id, credited_amount)
    logger.info(
        "Topup %s credited %s to user %s (new balance %s)",
        topup.id,
        credited_amount,
        topup.user_id,
        balance,
    )
    return balance


@router.callback_query(F.data.startswith("approve_topup:"))
async def approve_topup(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("شما دسترسی ندارید.", show_alert=True)
        return

    try:
        topup_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("درخواست پیدا نشد.", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(WalletTopUp).where(WalletTopUp.id == topup_id)
        )
        topup = result.scalar_one_or_none()

        if not topup or topup.status != "pending":
            await callback.answer("درخواست پیدا نشد یا قبلاً بررسی شده.", show_alert=True)
            return

        if not topup.receipt_file_id:
            await callback.answer("هنوز رسیدی دریافت نشده است.", show_alert=True)
            return

        amount = topup.requested_amount
        balance = await _credit_topup(session, topup, amount, callback.from_user.id)
        if balance is None:
            await session.rollback()
            await callback.answer("درخواست پیدا نشد یا قبلاً بررسی شده.", show_alert=True)
            return
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
                + f"\n\nتایید مبلغ درخواستی ({amount:,})"
            )
        except Exception:
            pass

        await callback.message.answer(
            get_text("admin_topup_credited_requested", amount=amount)
        )
        await callback.answer("شارژ تایید شد.")


@router.callback_query(F.data.startswith("manual_topup:"))
async def manual_topup_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("شما دسترسی ندارید.", show_alert=True)
        return

    try:
        topup_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("درخواست پیدا نشد.", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(WalletTopUp).where(WalletTopUp.id == topup_id)
        )
        topup = result.scalar_one_or_none()

        if not topup or topup.status != "pending":
            await callback.answer("درخواست پیدا نشد یا قبلاً بررسی شده.", show_alert=True)
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

    if is_nav_text(message.text):
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
        amount = int((message.text or "").replace(",", "").replace("،", "").strip())
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
            await message.answer("درخواست پیدا نشد یا قبلاً بررسی شده.")
            await state.clear()
            return

        requested = topup.requested_amount
        balance = await _credit_topup(session, topup, amount, message.from_user.id)
        if balance is None:
            await session.rollback()
            await message.answer("درخواست پیدا نشد یا قبلاً بررسی شده.")
            await state.clear()
            return
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
        await callback.answer("شما دسترسی ندارید.", show_alert=True)
        return

    try:
        topup_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("درخواست پیدا نشد.", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(WalletTopUp).where(WalletTopUp.id == topup_id)
        )
        topup = result.scalar_one_or_none()

        if not topup or topup.status != "pending":
            await callback.answer("درخواست پیدا نشد.", show_alert=True)
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
                caption=(callback.message.caption or "") + "\n\nرد شد"
            )
        except Exception:
            pass

        await callback.answer("درخواست شارژ رد شد.", show_alert=True)


async def _send_dashboard(bot, chat_id: int) -> None:
    from app.utils.statistics import get_dashboard_stats

    async with AsyncSessionLocal() as session:
        stats = await get_dashboard_stats(session)
        plans = await list_active_plans(session)
        stock_lines = []
        for plan in plans:
            n = await count_available(session, plan["id"])
            stock_lines.append(f"  {plan['name']}: {n} آزاد")
        if not stock_lines:
            stock_lines = ["  • —"]

        await bot.send_message(
            chat_id=chat_id,
            text=(
                "داشبورد مدیریت\n\n"
                f"کاربران: {stats['total_users']}\n"
                f"سفارشات: {stats['total_orders']}\n"
                f"پرداخت در انتظار: {stats['pending_payments']}\n"
                f"اکانت فعال: {stats['active_accounts']}\n"
                f"درآمد کل: {stats['total_revenue']:,} تومان\n\n"
                "موجودی کانفیگ:\n" + "\n".join(stock_lines) + "\n\n"
                "از منو «پنل ادمین» کانفیگ‌ها را مدیریت کنید."
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
            await bot.send_message(chat_id=chat_id, text="پرداخت در انتظاری وجود ندارد.")
            return

        text = "پرداخت‌های در انتظار:\n\n"
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
                        f"از کیف پول: {wallet_debit:,} | "
                        f"رسید: {amount_due:,}\n"
                    )
                text += (
                    f"پرداخت #{payment.id} | کاربر {payment.user_id}\n"
                    f"{plan_label} | {order.days} روز {order.traffic_gb}GB\n"
                    f"کل: {order.price:,} تومان\n"
                    f"{wallet_line}"
                    f"{'─' * 25}\n"
                )

        if topups:
            text += "\nشارژ کیف پول در انتظار:\n\n"
            for topup in topups:
                text += (
                    f"شارژ #{topup.id} | کاربر {topup.user_id}\n"
                    f"درخواستی: {topup.requested_amount:,} تومان\n"
                    f"{'─' * 25}\n"
                )

        await bot.send_message(chat_id=chat_id, text=text)


@router.callback_query(F.data == "admin:dashboard")
async def admin_dashboard_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    try:
        await _send_dashboard(callback.bot, callback.message.chat.id)
    except Exception as exc:
        logger.error(f"Error showing dashboard: {exc}")
        await callback.message.answer("خطا در نمایش داشبورد.")
    await callback.answer()


@router.callback_query(F.data == "admin:pending")
async def admin_pending_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    await _send_pending_payments(callback.bot, callback.message.chat.id)
    await callback.answer()


@router.message(Command("dashboard"))
async def show_dashboard(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("شما دسترسی ادمین ندارید.")
        return
    try:
        await _send_dashboard(message.bot, message.chat.id)
    except Exception as exc:
        logger.error(f"Error showing dashboard: {exc}")
        await message.answer("خطا در نمایش داشبورد.")


@router.message(Command("pending"))
async def show_pending_payments(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("شما دسترسی ادمین ندارید.")
        return
    await _send_pending_payments(message.bot, message.chat.id)


@router.message(Command("payments"))
async def show_payment_history(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("شما دسترسی ادمین ندارید.")
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Payment).order_by(Payment.created_at.desc()).limit(20)
        )
        payments = result.scalars().all()

        if not payments:
            await message.answer("پرداختی پیدا نشد.")
            return

        status_map = {"pending": "در انتظار", "approved": "تایید", "rejected": "رد"}
        text = "تاریخچه پرداخت‌ها:\n\n"
        for payment in payments:
            order_result = await session.execute(
                select(Order).where(Order.id == payment.order_id)
            )
            order = order_result.scalar_one_or_none()
            if order:
                icon = status_map.get(payment.status, "?")
                text += f"{icon} #{payment.id} | {order.price:,} تومان | {payment.created_at:%Y-%m-%d}\n"

        await message.answer(text)


# --- Admin: user editor ----------------------------------------------------


@router.callback_query(F.data == "admin:users")
async def admin_users_menu(callback: CallbackQuery, state: FSMContext):
    """Open the user editor."""
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return

    await state.set_state(AdminStates.waiting_for_user_lookup)
    await callback.answer()
    await callback.message.answer(
        "شماره کاربر یا نام کاربری او را بفرستید (یا «لغو»).",
        reply_markup=admin_cancel_keyboard(),
    )


@router.message(AdminStates.waiting_for_user_lookup)
async def admin_user_lookup_input(message: Message, state: FSMContext):
    """Take the customer id/username the admin typed."""
    if not is_admin(message.from_user.id):
        return

    text = (message.text or "").strip()
    if text == CANCEL_BUTTON:
        await state.clear()
        await message.answer(
            get_text("operation_cancelled"), reply_markup=ReplyKeyboardRemove()
        )
        return

    await state.clear()
    await _show_user_card(message, text)


@router.message(AdminStates.waiting_for_user_credit)
async def admin_user_credit(message: Message, state: FSMContext):
    """Top up a customer's wallet."""
    if not is_admin(message.from_user.id):
        return

    data = await state.get_data()
    target_id = data.get("admin_target_user_id")
    if not target_id:
        await message.answer(get_text("error_general"))
        await state.clear()
        return

    try:
        amount = int((message.text or "").replace(",", "").replace("،", "").strip())
    except (TypeError, ValueError):
        await message.answer(get_text("error_invalid_number"))
        return

    if amount < 1:
        await message.answer(get_text("error_invalid_number"))
        return

    async with AsyncSessionLocal() as session:
        await credit_balance(session, int(target_id), amount)
        await session.commit()
        summary = await get_user_summary(session, int(target_id))

    await state.clear()
    await message.answer(
        get_text("admin_user_credited", amount=amount),
        reply_markup=admin_user_keyboard(
            int(target_id), blocked=bool(summary.user.is_blocked)
        )
        if summary
        else None,
    )


@router.message(F.text == "/user")
async def admin_user_lookup(message: Message, state: FSMContext):
    """Look a customer up by id or username."""
    if not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(get_text("admin_user_not_found"))
        return
    await _show_user_card(message, parts[1].strip())


async def _show_user_card(message: Message, query: str) -> None:
    async with AsyncSessionLocal() as session:
        user = await find_user(session, query)
        if not user:
            await message.answer(get_text("admin_user_not_found"))
            return
        summary = await get_user_summary(session, user.id)
        blocked = bool(summary.user.is_blocked) if summary else False
        user_id = user.id

    await message.answer(
        get_text(
            "admin_user_summary",
            name=summary.display_name if summary else str(user_id),
            user_id=user_id,
            balance=summary.balance if summary else 0,
            total_orders=summary.total_orders if summary else 0,
            completed=summary.completed_orders if summary else 0,
            spent=summary.total_spent if summary else 0,
            accounts=summary.active_accounts if summary else 0,
            referrer=summary.referred_by if summary else "—",
            referrals=summary.referrals if summary else 0,
            blocked="بله" if blocked else "خیر",
        ),
        reply_markup=admin_user_keyboard(user_id, blocked=blocked),
    )


@router.callback_query(F.data.startswith("user:toggle_block:"))
async def admin_user_toggle_block(callback: CallbackQuery):
    """Block or unblock a customer."""
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return

    user_id = int(callback.data.split(":")[2])
    async with AsyncSessionLocal() as session:
        user = await set_user_blocked(session, user_id, True)
        if not user:
            await callback.answer(get_text("admin_user_not_found"), show_alert=True)
            return
        if user.is_blocked:
            await set_user_blocked(session, user_id, False)
            blocked = False
        else:
            blocked = True

    await callback.answer(
        get_text("admin_user_blocked" if blocked else "admin_user_unblocked")
    )
    await callback.message.edit_reply_markup(
        reply_markup=admin_user_keyboard(user_id, blocked=blocked)
    )


@router.callback_query(F.data.startswith("user:credit:"))
async def admin_user_credit_start(callback: CallbackQuery, state: FSMContext):
    """Ask the admin for the amount to credit."""
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return

    user_id = int(callback.data.split(":")[2])
    await state.update_data(admin_target_user_id=user_id)
    await state.set_state(AdminStates.waiting_for_user_credit)
    await callback.answer()
    await callback.message.answer(get_text("admin_credit_prompt"))


@router.callback_query(F.data.startswith("user:renew:"))
async def admin_user_renew(callback: CallbackQuery):
    """Renew a customer's account for free (admin gift)."""
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return

    user_id = int(callback.data.split(":")[2])
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(VPNAccount)
            .where(VPNAccount.user_id == user_id, VPNAccount.is_active.is_(True))
            .order_by(VPNAccount.created_at.desc())
            .limit(1)
        )
        account = result.scalar_one_or_none()
        if not account:
            await callback.answer("اکانت فعالی برای این کاربر نیست.", show_alert=True)
            return

        from app.services.renewal import extend_vpn_account

        order = Order(
            user_id=user_id,
            days=30,
            traffic_gb=0,
            price=0,
            original_price=0,
            renew_vpn_account_id=account.id,
            status="completed",
        )
        session.add(order)
        await session.flush()
        await extend_vpn_account(session, account, order)
        await session.commit()
        await session.refresh(account)

    await callback.answer("تمدید انجام شد.")
    await callback.message.answer(
        f"تمدید {account.order_id} انجام شد.\n"
        f"انقضای جدید: {account.expires_at:%Y-%m-%d}"
    )


# --- Admin: promo codes ----------------------------------------------------


@router.callback_query(F.data == "admin:promos")
async def admin_promos_menu(callback: CallbackQuery):
    """List the promo codes."""
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        promos = await list_promos(session)

    await callback.answer()
    if not promos:
        await callback.message.answer(
            "هنوز کد پرومویی ساخته نشده است.",
            reply_markup=admin_promo_keyboard(),
        )
        return

    await callback.message.answer(
        get_text(
            "admin_promo_list",
            list="\n".join(f"• {describe_promo(p)}" for p in promos),
        ),
        reply_markup=admin_promo_list_keyboard(promos),
    )


@router.callback_query(F.data == "promo:new")
async def admin_promo_new(callback: CallbackQuery, state: FSMContext):
    """Start creating a promo code."""
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return

    await state.set_state(AdminStates.waiting_for_promo_code)
    await callback.answer()
    await callback.message.answer(
        get_text("admin_promo_prompt"),
        reply_markup=admin_cancel_keyboard(),
    )


@router.message(AdminStates.waiting_for_promo_code)
async def admin_promo_code_input(message: Message, state: FSMContext):
    """Take the promo code, then ask for its kind."""
    if not is_admin(message.from_user.id):
        return

    code = (message.text or "").strip().upper()
    if not code:
        await message.answer(get_text("admin_promo_prompt"))
        return

    await state.update_data(promo_code=code)
    await state.set_state(AdminStates.waiting_for_promo_value)
    await message.answer(
        get_text("admin_promo_kind_prompt"),
        reply_markup=admin_promo_kind_keyboard(),
    )


@router.callback_query(F.data.startswith("promo:kind:"))
async def admin_promo_kind(callback: CallbackQuery, state: FSMContext):
    """Remember the promo kind and ask for the value."""
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return

    kind = callback.data.split(":")[2]
    if kind not in (PROMO_KIND_DAYS, PROMO_KIND_GB, PROMO_KIND_PERCENT):
        await callback.answer("نوع نامعتبر است.", show_alert=True)
        return

    data = await state.get_data()
    if not data.get("promo_code"):
        await callback.answer("ابتدا کد را وارد کنید.", show_alert=True)
        return

    await state.update_data(promo_kind=kind)
    await state.set_state(AdminStates.waiting_for_promo_value)
    await callback.answer()
    await callback.message.answer(
        get_text("admin_promo_value_prompt"),
        reply_markup=admin_cancel_keyboard(),
    )


@router.message(AdminStates.waiting_for_promo_value)
async def admin_promo_value(message: Message, state: FSMContext):
    """Create the promo code."""
    if not is_admin(message.from_user.id):
        return

    data = await state.get_data()
    code = data.get("promo_code")
    kind = data.get("promo_kind")
    if not code or not kind:
        await message.answer(get_text("error_general"))
        await state.clear()
        return

    try:
        value = int((message.text or "").strip())
    except (TypeError, ValueError):
        await message.answer(get_text("error_invalid_number"))
        return

    try:
        async with AsyncSessionLocal() as session:
            promo = await create_promo(
                session,
                code=code,
                kind=kind,
                value=value,
                created_by=message.from_user.id,
            )
            await session.commit()
            effect = describe_promo(promo)
    except ValueError as exc:
        await message.answer(str(exc))
        return

    await state.clear()
    await message.answer(
        get_text("admin_promo_created", code=promo.code, effect=effect),
        reply_markup=admin_promo_keyboard(),
    )


@router.callback_query(F.data.startswith("promo:toggle:"))
async def admin_promo_toggle(callback: CallbackQuery):
    """Enable or disable a promo code."""
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return

    promo_id = int(callback.data.split(":")[2])
    async with AsyncSessionLocal() as session:
        from sqlalchemy import select as sa_select

        from app.database.models import PromoCode

        result = await session.execute(
            sa_select(PromoCode).where(PromoCode.id == promo_id)
        )
        promo = result.scalar_one_or_none()
        if not promo:
            await callback.answer(get_text("admin_promo_not_found"), show_alert=True)
            return
        await set_promo_active(session, promo, not promo.is_active)
        state_label = "فعال" if promo.is_active else "خاموش"

    await callback.answer(get_text("admin_promo_toggled", code=promo.code, state=state_label))


# --- Admin: maintenance / backup -------------------------------------------


@router.callback_query(F.data == "admin:maintenance")
async def admin_maintenance_toggle(callback: CallbackQuery):
    """Turn the shop's maintenance mode on or off."""
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return

    from app.services.bot_settings import is_maintenance, set_bot_settings

    async with AsyncSessionLocal() as session:
        current = await is_maintenance(session)
        await set_bot_settings(session, maintenance_mode=not current)

    await callback.answer()
    await callback.message.answer(
        get_text("admin_maintenance_off" if current else "admin_maintenance_on")
    )


@router.callback_query(F.data == "admin:backup")
async def admin_backup(callback: CallbackQuery):
    """Send the admin a copy of the database."""
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return

    from app.services.backup import create_backup_bytes

    await callback.answer("در حال ساخت پشتیبان…")
    try:
        payload = await create_backup_bytes()
    except Exception as exc:
        logger.error("Database backup failed: %s", exc)
        await callback.message.answer(
            get_text("admin_backup_failed", error=exc)
        )
        return

    await callback.message.answer(
        get_text("admin_backup_ready"),
        reply_markup=ReplyKeyboardRemove(),
    )
    await callback.bot.send_document(
        chat_id=callback.from_user.id,
        document=(
            "db-backup.sqlite3",
            payload,
            "application/x-sqlite3",
        ),
    )


# --- Broadcast: preview / edit / per-user ----------------------------------


@router.callback_query(F.data == "broadcast:preview")
async def admin_broadcast_preview(callback: CallbackQuery, state: FSMContext):
    """Show the pending broadcast before it goes out."""
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return

    data = await state.get_data()
    text = data.get("broadcast_text")
    if not text:
        await callback.answer("متن پیام خالی است.", show_alert=True)
        return

    await callback.answer()
    await callback.message.answer(
        get_text("admin_broadcast_preview", text=text),
        reply_markup=admin_cancel_keyboard(),
    )


@router.callback_query(F.data == "broadcast:edit_last")
async def admin_broadcast_edit_last(callback: CallbackQuery):
    """Edit the messages of the last broadcast."""
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return

    from app.services.broadcast import edit_last_broadcast, last_broadcast

    async with AsyncSessionLocal() as session:
        broadcast = await last_broadcast(session)
        if not broadcast:
            await callback.answer("هنوز پیامی ارسال نشده است.", show_alert=True)
            return
        edited = await edit_last_broadcast(
            callback.bot,
            session,
            broadcast.text,
            use_html=broadcast.use_html,
        )

    await callback.answer(
        get_text("admin_broadcast_edited", count=edited), show_alert=True
    )


async def _send_broadcast_prompt(message: Message, state: FSMContext) -> None:
    from app.services.broadcast import list_recipient_ids

    async with AsyncSessionLocal() as session:
        recipients = await list_recipient_ids(session)

    await state.set_state(AdminStates.waiting_for_broadcast_text)
    await message.answer(
        f"پیام همگانی\n\n"
        f"تعداد کاربران: {len(recipients)}\n\n"
        "متن پیام را بفرستید. همین متن برای همه ارسال می‌شود.\n"
        f"برای انصراف: «{CANCEL_BUTTON}»",
        reply_markup=admin_broadcast_keyboard(),
    )


@router.callback_query(F.data == "admin:broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    await callback.message.edit_reply_markup(reply_markup=None)
    await _send_broadcast_prompt(callback.message, state)
    await callback.answer()


@router.message(Command("broadcast"))
async def broadcast_command(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("شما دسترسی ادمین ندارید.")
        return
    await _send_broadcast_prompt(message, state)


@router.message(AdminStates.waiting_for_broadcast_text)
async def admin_broadcast_send(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    if is_nav_text(message.text):
        await state.clear()
        await message.answer(
            "ارسال پیام همگانی لغو شد.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await show_admin_menu(message)
        return

    text = (message.text or "").strip()
    if not text:
        await message.answer("متن پیام خالی است. یک متن بفرستید.")
        return

    from app.services.broadcast import (
        BROADCAST_TARGET_ALL,
        create_broadcast,
        send_broadcast,
    )

    data = await state.get_data()
    use_html = bool(data.get("broadcast_html"))
    target = data.get("broadcast_target") or BROADCAST_TARGET_ALL
    target_chat_id = data.get("broadcast_target_chat_id")

    async with AsyncSessionLocal() as session:
        broadcast = await create_broadcast(
            session,
            text,
            use_html=use_html,
            target=target,
            target_chat_id=target_chat_id,
            created_by=message.from_user.id,
        )

    await state.clear()
    wait_msg = await message.answer("در حال ارسال…")

    async with AsyncSessionLocal() as session:
        result = await send_broadcast(message.bot, session, broadcast)

    try:
        await wait_msg.edit_text(f"ارسال تمام شد.\n\n{result.summary}")
    except Exception:
        await message.answer(f"ارسال تمام شد.\n\n{result.summary}")

    await show_admin_menu(message)
