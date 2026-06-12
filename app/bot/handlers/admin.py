from datetime import datetime, timedelta
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.texts import get_text
from app.config.settings import settings
from app.database.models import Order, Payment, VPNAccount
from app.database.session import AsyncSessionLocal
from app.bot.states import AdminStates

router = Router()


def is_admin(user_id: int) -> bool:
   """Check if user is admin"""
   return user_id in settings.ADMIN_IDS


@router.callback_query(F.data.startswith("approve_payment:"))
async def approve_payment(callback: CallbackQuery, state: FSMContext):
   """Approve payment - ask admin for subscription link"""
   if not is_admin(callback.from_user.id):
       await callback.answer("شما دسترسی ندارید!", show_alert=True)
       return
   
   payment_id = int(callback.data.split(":")[1])
   
   async with AsyncSessionLocal() as session:
       # Get payment and order
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
       
       # Store payment info in state and ask for subscription
       await state.update_data(payment_id=payment_id, order_id=order.id)
       await state.set_state(AdminStates.waiting_for_subscription)
       
       await callback.message.answer(
           f"✅ پرداخت تایید شد!\n\n"
           f"📦 جزئیات سفارش:\n"
           f"👤 کاربر: {order.user_id}\n"
           f"⏱ مدت: {order.days} روز\n"
           f"📊 حجم: {order.traffic_gb} گیگابایت\n\n"
           f"لطفاً لینک اشتراک (subscription link) را ارسال کنید:"
       )
       await callback.answer()


@router.message(AdminStates.waiting_for_subscription)
async def receive_subscription(message: Message, state: FSMContext):
   """Receive subscription link from admin and save it"""
   if not is_admin(message.from_user.id):
       return
   
   subscription_url = message.text.strip()
   data = await state.get_data()
   payment_id = data.get("payment_id")
   order_id = data.get("order_id")
   
   async with AsyncSessionLocal() as session:
       # Get payment and order
       result = await session.execute(select(Payment).where(Payment.id == payment_id))
       payment = result.scalar_one_or_none()
       
       result = await session.execute(select(Order).where(Order.id == order_id))
       order = result.scalar_one_or_none()
       
       if not payment or not order:
           await message.answer("❌ خطا: سفارش یا پرداخت یافت نشد!")
           await state.clear()
           return
       
       # Update payment status
       payment.status = "approved"
       payment.reviewed_at = datetime.utcnow()
       payment.reviewed_by = message.from_user.id
       
       # Update order status
       order.status = "completed"
       
       # Create VPN account record
       vpn_account = VPNAccount(
           order_id=order.id,
           user_id=order.user_id,
           xui_client_id="manual",
           subscription_path=subscription_url,
           expires_at=datetime.utcnow() + timedelta(days=order.days),
           traffic_limit_gb=order.traffic_gb,
           is_active=True
       )
       session.add(vpn_account)
       
       await session.commit()
       
       # Notify user
       try:
           await message.bot.send_message(
               chat_id=order.user_id,
               text=get_text("payment_approved", subscription_url=subscription_url)
           )
           await message.answer(
               f"✅ اشتراک با موفقیت ثبت شد و به کاربر ارسال شد!\n\n"
               f"👤 کاربر: {order.user_id}\n"
               f"🔗 لینک: {subscription_url}"
           )
       except Exception as e:
           print(f"Failed to notify user: {e}")
           await message.answer(f"⚠️ خطا در ارسال به کاربر: {e}")
   
   await state.clear()


@router.callback_query(F.data.startswith("reject_payment:"))
async def reject_payment(callback: CallbackQuery):
   """Reject payment"""
   if not is_admin(callback.from_user.id):
       await callback.answer("شما دسترسی ندارید!", show_alert=True)
       return
   
   payment_id = int(callback.data.split(":")[1])
   
   async with AsyncSessionLocal() as session:
       # Get payment and order
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
       
       # Update payment status
       payment.status = "rejected"
       payment.reviewed_at = datetime.utcnow()
       payment.reviewed_by = callback.from_user.id
       payment.admin_note = "رد شده توسط ادمین"
       
       # Update order status
       order.status = "rejected"
       
       await session.commit()
       
       # Notify user
       try:
           await callback.bot.send_message(
               chat_id=order.user_id,
               text=get_text("payment_rejected", reason="رسید پرداخت معتبر نیست")
           )
       except Exception as e:
           print(f"Failed to notify user: {e}")
       
       # Update admin message
       await callback.message.edit_caption(
           caption=callback.message.caption + "\n\n❌ رد شد توسط ادمین"
       )
       await callback.answer("پرداخت رد شد!", show_alert=True)


@router.message(F.text == "/dashboard")
async def show_dashboard(message: Message):
    """Show admin dashboard with statistics"""
    from app.utils.logger import logger
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
                f"• کل: {stats['total_revenue']:,} تومان\n"
            )
            
            await message.answer(dashboard_text)
            logger.info(f"Admin {message.from_user.id} viewed dashboard")
            
    except Exception as e:
        logger.error(f"Error showing dashboard: {e}")
        await message.answer("خطا در نمایش داشبورد!")


@router.message(F.text == "/pending")
async def show_pending_payments(message: Message):
    """Show all pending payments with bulk actions"""
    from app.utils.logger import logger
    
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
            
    except Exception as e:
        logger.error(f"Error showing pending payments: {e}")
        await message.answer("خطا در نمایش پرداخت‌ها!")


@router.message(F.text == "/payments")
async def show_payment_history(message: Message):
    """Show payment history"""
    from app.utils.logger import logger
    
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
                "rejected": "❌ رد شده"
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
            
    except Exception as e:
        logger.error(f"Error showing payment history: {e}")
        await message.answer("خطا در نمایش تاریخچه!")
