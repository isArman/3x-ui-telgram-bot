import pytest
from datetime import datetime, timedelta
from app.database.models import User, Order, Payment, VPNAccount
from app.database.session import AsyncSessionLocal, init_db
from sqlalchemy import select


@pytest.mark.asyncio
async def test_create_user():
    """Test creating a user"""
    await init_db()
    
    async with AsyncSessionLocal() as session:
        user = User(
            id=123456,
            username="testuser",
            first_name="Test",
            last_name="User"
        )
        session.add(user)
        await session.commit()
        
        # Verify user was created
        result = await session.execute(select(User).where(User.id == 123456))
        saved_user = result.scalar_one_or_none()
        
        assert saved_user is not None
        assert saved_user.username == "testuser"
        assert saved_user.first_name == "Test"
        assert saved_user.is_blocked is False


@pytest.mark.asyncio
async def test_create_order():
    """Test creating an order"""
    await init_db()
    
    async with AsyncSessionLocal() as session:
        order = Order(
            user_id=123456,
            days=30,
            traffic_gb=40,
            price=150000,
            status="pending"
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)
        
        assert order.id is not None
        assert order.days == 30
        assert order.traffic_gb == 40
        assert order.status == "pending"


@pytest.mark.asyncio
async def test_create_payment():
    """Test creating a payment"""
    await init_db()
    
    async with AsyncSessionLocal() as session:
        order = Order(
            user_id=123456,
            days=30,
            traffic_gb=40,
            price=150000
        )
        session.add(order)
        await session.flush()
        
        payment = Payment(
            order_id=order.id,
            user_id=123456,
            receipt_file_id="file123",
            status="pending"
        )
        session.add(payment)
        await session.commit()
        await session.refresh(payment)
        
        assert payment.id is not None
        assert payment.status == "pending"
        assert payment.reviewed_at is None


@pytest.mark.asyncio
async def test_create_vpn_account():
    """Test creating a VPN account"""
    await init_db()
    
    async with AsyncSessionLocal() as session:
        order = Order(
            user_id=123456,
            days=30,
            traffic_gb=40,
            price=150000
        )
        session.add(order)
        await session.flush()
        
        vpn_account = VPNAccount(
            order_id=order.id,
            user_id=123456,
            xui_client_id="uuid-123",
            subscription_path="https://example.com/sub/uuid-123",
            expires_at=datetime.utcnow() + timedelta(days=30),
            traffic_limit_gb=40,
            is_active=True
        )
        session.add(vpn_account)
        await session.commit()
        await session.refresh(vpn_account)
        
        assert vpn_account.id is not None
        assert vpn_account.is_active is True
        assert vpn_account.expiry_notified is False
        assert vpn_account.last_renewed_at is None


@pytest.mark.asyncio
async def test_vpn_account_notification_fields():
    """Test VPN account notification tracking fields"""
    await init_db()
    
    async with AsyncSessionLocal() as session:
        order = Order(user_id=123, days=30, traffic_gb=40, price=100000)
        session.add(order)
        await session.flush()
        
        vpn = VPNAccount(
            order_id=order.id,
            user_id=123,
            xui_client_id="test",
            subscription_path="https://test.com",
            expires_at=datetime.utcnow() + timedelta(days=2),
            traffic_limit_gb=40
        )
        session.add(vpn)
        await session.commit()
        
        # Mark as notified
        vpn.expiry_notified = True
        vpn.last_renewed_at = datetime.utcnow()
        await session.commit()
        await session.refresh(vpn)
        
        assert vpn.expiry_notified is True
        assert vpn.last_renewed_at is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
