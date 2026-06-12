from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.bot.keyboards.admin import panel_config_keyboard, payment_review_keyboard
from app.bot.states import AdminStates
from app.config.settings import settings
from app.config.texts import get_text
from app.database.models import Order, Payment, VPNAccount
from app.database.session import AsyncSessionLocal
from app.utils.logger import logger
from app.xui.service import (
    build_xui_client,
    create_provision_job,
    get_effective_panel_config,
    get_or_create_panel_config,
    provision_vpn_account,
)

router = Router()


def is_admin(user_id: int) -> bool:
    """Check if user is admin"""
    return user_id in settings.ADMIN_IDS


def mask_password(password: str) -> str:
    if not password:
        return "تنظیم نشده"
    if len(password) <= 2:
        return "*" * len(password)
    return password[0] + ("*" * (len(password) - 2)) + password[-1]


async def format_panel_status(session) -> str:
    config = await get_effective_panel_config(session)
    auto_status = "فعال ✅" if config.auto_create else "غیرفعال ⏸"
    configured = "بله ✅" if config.is_configured else "خیر ❌"
    mode_label = "Remote (worker ایران)" if config.is_remote_mode else "Direct (از همین سرور)"

    return (
        "⚙️ تنظیمات پنل 3x-ui\n\n"
        f"🌍 حالت: {mode_label}\n"
        f"🌐 URL عمومی (subscription): {config.public_url or 'تنظیم نشده'}\n"
        f"🔧 URL API (direct): {config.url or 'تنظیم نشده'}\n"
        f"👤 نام کاربری: {config.username or 'تنظیم نشده'}\n"
        f"🔑 رمز عبور: {mask_password(config.password)}\n"
        f"📡 Inbound ID: {config.inbound_id}\n"
        f"🔗 پنل کامل: {configured}\n"
        f"🤖 ساخت خودکار اکانت: {auto_status}\n\n"
        "Remote: bot در آلمان + worker روی سرور ایران (localhost)\n"
        "Direct: bot مستقیم به API پنل وصل می‌شود."
    )


def panel_keyboard(config):
    return panel_config_keyboard(config.auto_create, config.provision_mode)


async def complete_payment_approval(
    session,
    payment: Payment,
    order: Order,
    admin_id: int,
    subscription_url: str,
    xui_client_id: str,
) -> None:
    payment.status = "approved"
    payment.reviewed_at = datetime.utcnow()
    payment.reviewed_by = admin_id
    order.status = "completed"

    vpn_account = VPNAccount(
        order_id=order.id,
        user_id=order.user_id,
        xui_client_id=xui_client_id,
        subscription_path=subscription_url,
        expires_at=datetime.utcnow() + timedelta(days=order.days),
        traffic_limit_gb=order.traffic_gb,
        is_active=True,
    )
    session.add(vpn_account)
    await session.commit()


@router.message(F.text == "/panel")
async def show_panel_config(message: Message):
    """Show 3x-ui panel configuration."""
    if not is_admin(message.from_user.id):
        return

    async with AsyncSessionLocal() as session:
        await get_or_create_panel_config(session)
        text = await format_panel_status(session)
        config = await get_effective_panel_config(session)
        await message.answer(text, reply_markup=panel_keyboard(config))


@router.callback_query(F.data == "panel:set_public_url")
async def panel_set_public_url(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("شما دسترسی ندارید!", show_alert=True)
        return

    await state.set_state(AdminStates.waiting_for_panel_public_url)
    await callback.message.answer(
        "🌐 URL عمومی پنل را ارسال کنید (برای لینک subscription کاربران).\n\n"
        "مثال: https://poloiians.faghat5k.ir:2053/YJBJbvcdMmIAnCYoAN"
    )
    await callback.answer()


@router.callback_query(F.data == "panel:set_url")
async def panel_set_url(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("شما دسترسی ندارید!", show_alert=True)
        return

    await state.set_state(AdminStates.waiting_for_panel_url)
    await callback.message.answer(
        "🔧 URL API پنل را ارسال کنید (فقط حالت Direct).\n\n"
        "مثال: https://panel.example.com:2053/path"
    )
    await callback.answer()


@router.callback_query(F.data == "panel:set_username")
async def panel_set_username(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("شما دسترسی ندارید!", show_alert=True)
        return

    await state.set_state(AdminStates.waiting_for_panel_username)
    await callback.message.answer("👤 نام کاربری پنل را ارسال کنید:")
    await callback.answer()


@router.callback_query(F.data == "panel:set_password")
async def panel_set_password(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("شما دسترسی ندارید!", show_alert=True)
        return

    await state.set_state(AdminStates.waiting_for_panel_password)
    await callback.message.answer("🔑 رمز عبور پنل را ارسال کنید:")
    await callback.answer()


@router.callback_query(F.data == "panel:set_inbound")
async def panel_set_inbound(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("شما دسترسی ندارید!", show_alert=True)
        return

    await state.set_state(AdminStates.waiting_for_panel_inbound_id)
    await callback.message.answer("📡 شناسه Inbound را ارسال کنید (عدد، مثلاً 1):")
    await callback.answer()


@router.callback_query(F.data == "panel:toggle_auto")
async def panel_toggle_auto(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("شما دسترسی ندارید!", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        panel = await get_or_create_panel_config(session)
        config = await get_effective_panel_config(session)

        if not config.is_configured:
            await callback.answer("ابتدا نام کاربری و رمز عبور را تنظیم کنید.", show_alert=True)
            return

        if config.is_remote_mode and not config.public_url:
            await callback.answer("در حالت Remote ابتدا URL عمومی را تنظیم کنید.", show_alert=True)
            return

        panel.auto_create = not panel.auto_create
        panel.updated_by = callback.from_user.id
        await session.commit()

        config = await get_effective_panel_config(session)
        text = await format_panel_status(session)
        await callback.message.edit_text(text, reply_markup=panel_keyboard(config))
        status = "فعال" if config.auto_create else "غیرفعال"
        await callback.answer(f"ساخت خودکار {status} شد.")


@router.callback_query(F.data == "panel:toggle_mode")
async def panel_toggle_mode(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("شما دسترسی ندارید!", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        panel = await get_or_create_panel_config(session)
        panel.provision_mode = "remote" if panel.provision_mode != "remote" else "direct"
        panel.updated_by = callback.from_user.id
        await session.commit()

        config = await get_effective_panel_config(session)
        text = await format_panel_status(session)
        await callback.message.edit_text(text, reply_markup=panel_keyboard(config))
        mode = "Remote" if config.is_remote_mode else "Direct"
        await callback.answer(f"حالت {mode} فعال شد.")


@router.callback_query(F.data == "panel:test_connection")
async def panel_test_connection(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("شما دسترسی ندارید!", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        config = await get_effective_panel_config(session)
        if not config.is_configured:
            await callback.answer("پنل هنوز تنظیم نشده است.", show_alert=True)
            return

        if config.is_remote_mode:
            await callback.message.answer(
                "ℹ️ در حالت Remote، تست اتصال باید روی سرور worker (ایران) انجام شود.\n"
                "دستور: `python -m app.worker.main` با XUI_URL=http://127.0.0.1:..."
            )
            await callback.answer("Remote mode")
            return

        if not config.url:
            await callback.answer("ابتدا URL API را تنظیم کنید.", show_alert=True)
            return

        client = build_xui_client(config)
        success, message = await client.test_connection()

    if success:
        await callback.message.answer(f"✅ {message}")
        await callback.answer("اتصال موفق")
    else:
        await callback.message.answer(f"❌ {message}")
        await callback.answer("اتصال ناموفق", show_alert=True)


@router.message(AdminStates.waiting_for_panel_url)
async def receive_panel_url(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    url = message.text.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        await message.answer("❌ URL باید با http:// یا https:// شروع شود.")
        return

    async with AsyncSessionLocal() as session:
        panel = await get_or_create_panel_config(session)
        panel.url = url
        panel.updated_by = message.from_user.id
        await session.commit()
        config = await get_effective_panel_config(session)
        await message.answer(
            f"✅ URL ذخیره شد.\n\n{await format_panel_status(session)}",
            reply_markup=panel_keyboard(config),
        )

    await state.clear()


@router.message(AdminStates.waiting_for_panel_public_url)
async def receive_panel_public_url(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    url = message.text.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        await message.answer("❌ URL باید با http:// یا https:// شروع شود.")
        return

    async with AsyncSessionLocal() as session:
        panel = await get_or_create_panel_config(session)
        panel.public_url = url
        panel.updated_by = message.from_user.id
        await session.commit()
        config = await get_effective_panel_config(session)
        await message.answer(
            f"✅ URL عمومی ذخیره شد.\n\n{await format_panel_status(session)}",
            reply_markup=panel_keyboard(config),
        )

    await state.clear()


@router.message(AdminStates.waiting_for_panel_username)
async def receive_panel_username(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    username = message.text.strip()
    if not username:
        await message.answer("❌ نام کاربری نمی‌تواند خالی باشد.")
        return

    async with AsyncSessionLocal() as session:
        panel = await get_or_create_panel_config(session)
        panel.username = username
        panel.updated_by = message.from_user.id
        await session.commit()
        config = await get_effective_panel_config(session)
        await message.answer(
            f"✅ نام کاربری ذخیره شد.\n\n{await format_panel_status(session)}",
            reply_markup=panel_keyboard(config),
        )

    await state.clear()


@router.message(AdminStates.waiting_for_panel_password)
async def receive_panel_password(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    password = message.text.strip()
    if not password:
        await message.answer("❌ رمز عبور نمی‌تواند خالی باشد.")
        return

    async with AsyncSessionLocal() as session:
        panel = await get_or_create_panel_config(session)
        panel.password = password
        panel.updated_by = message.from_user.id
        await session.commit()
        config = await get_effective_panel_config(session)
        await message.answer(
            f"✅ رمز عبور ذخیره شد.\n\n{await format_panel_status(session)}",
            reply_markup=panel_keyboard(config),
        )

    await state.clear()


@router.message(AdminStates.waiting_for_panel_inbound_id)
async def receive_panel_inbound_id(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    try:
        inbound_id = int(message.text.strip())
        if inbound_id < 1:
            raise ValueError
    except ValueError:
        await message.answer("❌ لطفاً یک عدد صحیح مثبت وارد کنید.")
        return

    async with AsyncSessionLocal() as session:
        panel = await get_or_create_panel_config(session)
        panel.inbound_id = inbound_id
        panel.updated_by = message.from_user.id
        await session.commit()
        config = await get_effective_panel_config(session)
        await message.answer(
            f"✅ Inbound ID ذخیره شد.\n\n{await format_panel_status(session)}",
            reply_markup=panel_keyboard(config),
        )

    await state.clear()


@router.callback_query(F.data.startswith("approve_payment:"))
async def approve_payment(callback: CallbackQuery, state: FSMContext):
    """Approve payment and auto-create VPN account when panel is configured."""
    if not is_admin(callback.from_user.id):
        await callback.answer("شما دسترسی ندارید!", show_alert=True)
        return

    payment_id = int(callback.data.split(":")[1])

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Payment).where(Payment.id == payment_id))
        payment = result.scalar_one_or_none()

        if not payment:
            await callback.answer("پرداخت یافت نشد!", show_alert=True)
            return

        if payment.status != "pending":
            await callback.answer("این پرداخت قبلاً بررسی شده است!", show_alert=True)
            return

        result = await session.execute(select(Order).where(Order.id == payment.order_id))
        order = result.scalar_one_or_none()

        if not order:
            await callback.answer("سفارش یافت نشد!", show_alert=True)
            return

        config = await get_effective_panel_config(session)

        if config.can_auto_create:
            if config.is_remote_mode:
                if not settings.WORKER_SECRET:
                    await callback.answer("WORKER_SECRET تنظیم نشده!", show_alert=True)
                    await callback.message.answer(
                        "❌ برای حالت Remote باید WORKER_SECRET در .env سرور bot تنظیم شود."
                    )
                    return

                payment.status = "processing"
                await session.commit()

                job = await create_provision_job(
                    session,
                    payment_id=payment.id,
                    order_id=order.id,
                    user_id=order.user_id,
                    days=order.days,
                    traffic_gb=order.traffic_gb,
                    admin_id=callback.from_user.id,
                )

                await callback.message.answer(
                    "✅ پرداخت تایید شد و در صف worker ایران قرار گرفت.\n\n"
                    f"🆔 Job #{job.id}\n"
                    f"👤 کاربر: {order.user_id}\n"
                    f"⏱ {order.days} روز | 📊 {order.traffic_gb} GB\n\n"
                    "پس از ساخت اکانت توسط worker، لینک به کاربر ارسال می‌شود."
                )

                try:
                    await callback.message.edit_caption(
                        caption=(callback.message.caption or "") + "\n\n⏳ در صف worker"
                    )
                except Exception:
                    pass

                await callback.answer("در صف worker")
                logger.info(f"Queued provision job {job.id} for order {order.id}")
                return

            provision_result, error = await provision_vpn_account(
                session,
                user_id=order.user_id,
                days=order.days,
                traffic_gb=order.traffic_gb,
            )

            if not provision_result:
                await callback.answer("ساخت خودکار ناموفق بود!", show_alert=True)
                await callback.message.answer(
                    f"❌ ساخت خودکار اکانت ناموفق بود.\n\n{error}\n\n"
                    "لطفاً با /panel اتصال را بررسی کنید یا لینک را دستی ارسال کنید."
                )
                await state.update_data(payment_id=payment_id, order_id=order.id)
                await state.set_state(AdminStates.waiting_for_subscription)
                return

            subscription_url = provision_result["subscription_url"]
            await complete_payment_approval(
                session,
                payment,
                order,
                callback.from_user.id,
                subscription_url,
                provision_result["uuid"],
            )

            try:
                await callback.bot.send_message(
                    chat_id=order.user_id,
                    text=get_text("payment_approved", subscription_url=subscription_url),
                )
            except Exception as exc:
                logger.error(f"Failed to notify user {order.user_id}: {exc}")

            await callback.message.answer(
                "✅ پرداخت تایید و اکانت به صورت خودکار ساخته شد!\n\n"
                f"👤 کاربر: {order.user_id}\n"
                f"📧 Email: {provision_result['email']}\n"
                f"🔗 لینک: {subscription_url}"
            )

            try:
                await callback.message.edit_caption(
                    caption=(callback.message.caption or "") + "\n\n✅ تایید و ساخت خودکار"
                )
            except Exception:
                pass

            await callback.answer("اکانت خودکار ساخته شد!")
            logger.info(f"Auto-provisioned account for order {order.id} by admin {callback.from_user.id}")
            return

        await state.update_data(payment_id=payment_id, order_id=order.id)
        await state.set_state(AdminStates.waiting_for_subscription)

        await callback.message.answer(
            f"✅ پرداخت تایید شد!\n\n"
            f"📦 جزئیات سفارش:\n"
            f"👤 کاربر: {order.user_id}\n"
            f"⏱ مدت: {order.days} روز\n"
            f"📊 حجم: {order.traffic_gb} گیگابایت\n\n"
            f"⚠️ ساخت خودکار غیرفعال است.\n"
            f"لطفاً لینک اشتراک (subscription link) را ارسال کنید:\n\n"
            f"برای فعال‌سازی: /panel"
        )
        await callback.answer()


@router.message(AdminStates.waiting_for_subscription)
async def receive_subscription(message: Message, state: FSMContext):
    """Receive subscription link from admin and save it (manual mode)."""
    if not is_admin(message.from_user.id):
        return

    subscription_url = message.text.strip()
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
            session,
            payment,
            order,
            message.from_user.id,
            subscription_url,
            "manual",
        )

        try:
            await message.bot.send_message(
                chat_id=order.user_id,
                text=get_text("payment_approved", subscription_url=subscription_url),
            )
            await message.answer(
                f"✅ اشتراک با موفقیت ثبت شد و به کاربر ارسال شد!\n\n"
                f"👤 کاربر: {order.user_id}\n"
                f"🔗 لینک: {subscription_url}"
            )
        except Exception as exc:
            logger.error(f"Failed to notify user: {exc}")
            await message.answer(f"⚠️ خطا در ارسال به کاربر: {exc}")

    await state.clear()


@router.callback_query(F.data.startswith("reject_payment:"))
async def reject_payment(callback: CallbackQuery):
    """Reject payment"""
    if not is_admin(callback.from_user.id):
        await callback.answer("شما دسترسی ندارید!", show_alert=True)
        return

    payment_id = int(callback.data.split(":")[1])

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Payment).where(Payment.id == payment_id))
        payment = result.scalar_one_or_none()

        if not payment:
            await callback.answer("پرداخت یافت نشد!", show_alert=True)
            return

        if payment.status != "pending":
            await callback.answer("این پرداخت قبلاً بررسی شده است!", show_alert=True)
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
                caption=(callback.message.caption or "") + "\n\n❌ رد شد توسط ادمین"
            )
        except Exception:
            pass

        await callback.answer("پرداخت رد شد!", show_alert=True)


@router.message(F.text == "/dashboard")
async def show_dashboard(message: Message):
    """Show admin dashboard with statistics"""
    from app.utils.statistics import get_dashboard_stats

    if not is_admin(message.from_user.id):
        return

    try:
        async with AsyncSessionLocal() as session:
            stats = await get_dashboard_stats(session)

            dashboard_text = (
                "📊 داشبورد مدیریت\n\n"
                "👥 کاربران و سفارشات:\n"
                f"• کل کاربران: {stats['total_users']}\n"
                f"• کل سفارشات: {stats['total_orders']}\n"
                f"• سفارشات امروز: {stats['today_orders']}\n"
                f"• پرداخت‌های در انتظار: {stats['pending_payments']}\n\n"
                "💳 اکانت‌ها:\n"
                f"• اکانت‌های فعال: {stats['active_accounts']}\n"
                f"• در حال انقضا (3 روز): {stats['expiring_soon']}\n\n"
                "💰 درآمد:\n"
                f"• امروز: {stats['today_revenue']:,} تومان\n"
                f"• هفته اخیر: {stats['weekly_revenue']:,} تومان\n"
                f"• ماه اخیر: {stats['monthly_revenue']:,} تومان\n"
                f"• کل: {stats['total_revenue']:,} تومان\n\n"
                "⚙️ /panel — تنظیمات پنل 3x-ui"
            )

            await message.answer(dashboard_text)
            logger.info(f"Admin {message.from_user.id} viewed dashboard")

    except Exception as exc:
        logger.error(f"Error showing dashboard: {exc}")
        await message.answer("خطا در نمایش داشبورد!")


@router.message(F.text == "/pending")
async def show_pending_payments(message: Message):
    """Show all pending payments"""
    if not is_admin(message.from_user.id):
        return

    try:
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
                    text += (
                        f"🆔 پرداخت #{payment.id}\n"
                        f"👤 کاربر: {payment.user_id}\n"
                        f"📦 سفارش: {order.days} روز، {order.traffic_gb} گیگ\n"
                        f"💰 مبلغ: {order.price:,} تومان\n"
                        f"📅 {payment.created_at.strftime('%Y-%m-%d %H:%M')}\n"
                        f"{'─' * 30}\n"
                    )

            await message.answer(text)
            logger.info(f"Admin {message.from_user.id} viewed pending payments")

    except Exception as exc:
        logger.error(f"Error showing pending payments: {exc}")
        await message.answer("خطا در نمایش پرداخت‌ها!")


@router.message(F.text == "/payments")
async def show_payment_history(message: Message):
    """Show payment history"""
    if not is_admin(message.from_user.id):
        return

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Payment)
                .order_by(Payment.created_at.desc())
                .limit(20)
            )
            payments = result.scalars().all()

            if not payments:
                await message.answer("هیچ پرداختی یافت نشد.")
                return

            text = "📜 تاریخچه پرداخت‌ها (20 تای آخر):\n\n"

            status_map = {
                "pending": "⏳ در انتظار",
                "approved": "✅ تایید شده",
                "rejected": "❌ رد شده",
            }

            for payment in payments:
                order_result = await session.execute(
                    select(Order).where(Order.id == payment.order_id)
                )
                order = order_result.scalar_one_or_none()

                status = status_map.get(payment.status, payment.status)

                if order:
                    text += (
                        f"🆔 #{payment.id} - {status}\n"
                        f"👤 کاربر: {payment.user_id}\n"
                        f"💰 {order.price:,} تومان\n"
                        f"📅 {payment.created_at.strftime('%Y-%m-%d')}\n"
                        f"{'─' * 25}\n"
                    )

            await message.answer(text)
            logger.info(f"Admin {message.from_user.id} viewed payment history")

    except Exception as exc:
        logger.error(f"Error showing payment history: {exc}")
        await message.answer("خطا در نمایش تاریخچه!")
