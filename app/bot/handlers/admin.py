from datetime import datetime, timedelta
from aiogram import Router, F
from aiogram.types import CallbackQuery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.texts import get_text
from app.config.settings import settings
from app.database.models import Order, Payment, VPNAccount
from app.database.session import AsyncSessionLocal
from app.xui.client import xui_client

router = Router()


def is_admin(user_id: int) -> bool:
    """Check if user is admin"""
    return user_id in settings.ADMIN_IDS


@router.callback_query(F.data.startswith("approve_payment:"))
async def approve_payment(callback: CallbackQuery):
    """Approve payment and create VPN account"""
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
        
        # Create VPN account in 3x-ui
        email = f"tg_{order.user_id}"
        
        vpn_result = await xui_client.add_client(
            email=email,
            traffic_gb=order.traffic_gb,
            expire_days=order.days
        )
        
        if not vpn_result:
            await callback.answer("خطا در ایجاد اکانت VPN!", show_alert=True)
            return
        
        # Get client UUID from result
        client_uuid = vpn_result.get("uuid")
        
        # Get subscription path
        subscription_path = await xui_client.get_client_subscription(client_uuid)
        subscription_url = f"{settings.XUI_URL}{subscription_path}"
        
        # Update payment status
        payment.status = "approved"
        payment.reviewed_at = datetime.utcnow()
        payment.reviewed_by = callback.from_user.id
        
        # Update order status
        order.status = "completed"
        
        # Create VPN account record
        vpn_account = VPNAccount(
            order_id=order.id,
            user_id=order.user_id,
            xui_client_id=client_uuid,
            subscription_path=subscription_path,
            expires_at=datetime.utcnow() + timedelta(days=order.days),
            traffic_limit_gb=order.traffic_gb,
            is_active=True
        )
        session.add(vpn_account)
        
        await session.commit()
        
        # Notify user
        try:
            await callback.bot.send_message(
                chat_id=order.user_id,
                text=get_text("payment_approved", subscription_url=subscription_url)
            )
        except Exception as e:
            print(f"Failed to notify user: {e}")
        
        # Update admin message
        await callback.message.edit_caption(
            caption=callback.message.caption + "\n\n✅ تایید شد توسط ادمین"
        )
        await callback.answer("پرداخت تایید شد و اکانت ایجاد شد!", show_alert=True)


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
