from datetime import datetime, timedelta
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.models import Order, Payment, VPNAccount, User


async def get_dashboard_stats(session: AsyncSession) -> dict:
    """Get dashboard statistics for admin"""
    
    now = datetime.utcnow()
    today_start = datetime(now.year, now.month, now.day)
    week_start = now - timedelta(days=7)
    month_start = now - timedelta(days=30)
    
    # Total users
    result = await session.execute(select(func.count(User.id)))
    total_users = result.scalar() or 0
    
    # Total orders
    result = await session.execute(select(func.count(Order.id)))
    total_orders = result.scalar() or 0
    
    # Today's orders
    result = await session.execute(
        select(func.count(Order.id)).where(Order.created_at >= today_start)
    )
    today_orders = result.scalar() or 0
    
    # Pending payments
    result = await session.execute(
        select(func.count(Payment.id)).where(Payment.status == "pending")
    )
    pending_payments = result.scalar() or 0
    
    # Active VPN accounts
    result = await session.execute(
        select(func.count(VPNAccount.id)).where(
            VPNAccount.is_active == True,
            VPNAccount.expires_at > now
        )
    )
    active_accounts = result.scalar() or 0
    
    # Expiring soon (within 3 days)
    three_days = now + timedelta(days=3)
    result = await session.execute(
        select(func.count(VPNAccount.id)).where(
            VPNAccount.is_active == True,
            VPNAccount.expires_at > now,
            VPNAccount.expires_at <= three_days
        )
    )
    expiring_soon = result.scalar() or 0
    
    # Today's revenue
    result = await session.execute(
        select(func.sum(Order.price)).where(
            Order.created_at >= today_start,
            Order.status == "completed"
        )
    )
    today_revenue = result.scalar() or 0
    
    # Weekly revenue
    result = await session.execute(
        select(func.sum(Order.price)).where(
            Order.created_at >= week_start,
            Order.status == "completed"
        )
    )
    weekly_revenue = result.scalar() or 0
    
    # Monthly revenue
    result = await session.execute(
        select(func.sum(Order.price)).where(
            Order.created_at >= month_start,
            Order.status == "completed"
        )
    )
    monthly_revenue = result.scalar() or 0
    
    # Total revenue
    result = await session.execute(
        select(func.sum(Order.price)).where(Order.status == "completed")
    )
    total_revenue = result.scalar() or 0
    
    return {
        "total_users": total_users,
        "total_orders": total_orders,
        "today_orders": today_orders,
        "pending_payments": pending_payments,
        "active_accounts": active_accounts,
        "expiring_soon": expiring_soon,
        "today_revenue": today_revenue,
        "weekly_revenue": weekly_revenue,
        "monthly_revenue": monthly_revenue,
        "total_revenue": total_revenue,
    }
