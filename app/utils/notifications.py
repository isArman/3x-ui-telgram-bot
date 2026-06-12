from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram import Bot
from app.database.models import VPNAccount
from app.utils.logger import logger


async def check_expiring_accounts(session: AsyncSession, bot: Bot):
    """Check for accounts expiring soon and send notifications"""
    
    now = datetime.utcnow()
    three_days_ahead = now + timedelta(days=3)
    
    # Find accounts expiring in 3 days that haven't been notified
    result = await session.execute(
        select(VPNAccount).where(
            VPNAccount.is_active == True,
            VPNAccount.expires_at > now,
            VPNAccount.expires_at <= three_days_ahead,
            VPNAccount.expiry_notified == False
        )
    )
    accounts = result.scalars().all()
    
    notified_count = 0
    
    for account in accounts:
        try:
            days_left = (account.expires_at - now).days
            
            message = (
                f"⚠️ هشدار انقضای اکانت\n\n"
                f"اکانت شما (سفارش #{account.order_id}) در {days_left} روز دیگر منقضی می‌شود.\n\n"
                f"📅 تاریخ انقضا: {account.expires_at.strftime('%Y-%m-%d')}\n"
                f"📊 حجم: {account.traffic_limit_gb} گیگابایت\n\n"
                f"برای تمدید اکانت با ادمین تماس بگیرید."
            )
            
            await bot.send_message(chat_id=account.user_id, text=message)
            
            # Mark as notified
            account.expiry_notified = True
            notified_count += 1
            
            logger.info(f"Sent expiry notification for account {account.id} to user {account.user_id}")
            
        except Exception as e:
            logger.error(f"Failed to send notification for account {account.id}: {e}")
    
    if notified_count > 0:
        await session.commit()
        logger.info(f"Sent {notified_count} expiry notifications")
    
    return notified_count
