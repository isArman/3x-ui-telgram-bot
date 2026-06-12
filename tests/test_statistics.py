import pytest
from datetime import datetime, timedelta
from sqlalchemy import select
from app.utils.statistics import get_dashboard_stats
from app.database.models import User, Order, Payment, VPNAccount
from app.database.session import AsyncSessionLocal, init_db


@pytest.mark.asyncio
async def test_dashboard_stats_empty_database():
    """Test dashboard stats with empty database"""
    await init_db()
    
    async with AsyncSessionLocal() as session:
        stats = await get_dashboard_stats(session)
        
        assert stats['total_users'] == 0
        assert stats['total_orders'] == 0
        assert stats['today_orders'] == 0
        assert stats['pending_payments'] == 0
        assert stats['active_accounts'] == 0
        assert stats['expiring_soon'] == 0
        assert stats['today_revenue'] == 0
        assert stats['total_revenue'] == 0


@pytest.mark.asyncio
async def test_dashboard_stats_with_data():
    """Test dashboard stats with sample data"""
    await init_db()
    
    async with AsyncSessionLocal() as session:
        # Create test user
        user = User(
            id=123456789,
            username="testuser",
            first_name="Test",
            last_name="User"
        )
        session.add(user)
        
        # Create test order
        order = Order(
            user_id=123456789,
            days=30,
            traffic_gb=40,
            price=150000,
            status="completed"
        )
        session.add(order)
        await session.flush()
        
        # Create test payment
        payment = Payment(
            order_id=order.id,
            user_id=123456789,
            receipt_file_id="test_file_id",
            status="pending"
        )
        session.add(payment)
        
        # Create test VPN account
        vpn_account = VPNAccount(
            order_id=order.id,
            user_id=123456789,
            xui_client_id="test_uuid",
            subscription_path="https://example.com/sub/test",
            expires_at=datetime.utcnow() + timedelta(days=30),
            traffic_limit_gb=40,
            is_active=True
        )
        session.add(vpn_account)
        
        await session.commit()
        
        # Get stats
        stats = await get_dashboard_stats(session)
        
        assert stats['total_users'] >= 1
        assert stats['total_orders'] >= 1
        assert stats['pending_payments'] >= 1
        assert stats['active_accounts'] >= 1
        assert stats['total_revenue'] >= 150000


@pytest.mark.asyncio
async def test_expiring_soon_accounts():
    """Test detection of accounts expiring soon"""
    await init_db()
    
    async with AsyncSessionLocal() as session:
        user = User(id=999999, username="expiring_user")
        session.add(user)
        await session.flush()
        
        order = Order(
            user_id=999999,
            days=3,
            traffic_gb=10,
            price=50000,
            status="completed"
        )
        session.add(order)
        await session.flush()
        
        # Account expiring in 2 days
        vpn_account = VPNAccount(
            order_id=order.id,
            user_id=999999,
            xui_client_id="expiring_uuid",
            subscription_path="https://example.com/sub/expiring",
            expires_at=datetime.utcnow() + timedelta(days=2),
            traffic_limit_gb=10,
            is_active=True
        )
        session.add(vpn_account)
        await session.commit()
        
        # Get stats
        stats = await get_dashboard_stats(session)
        
        assert stats['expiring_soon'] >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
