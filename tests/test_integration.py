import pytest
from datetime import datetime, timedelta
from app.database.models import User, Order, Payment, VPNAccount
from app.database.session import AsyncSessionLocal, init_db
from sqlalchemy import select


@pytest.mark.asyncio
async def test_complete_order_flow():
    """Test complete order flow from creation to VPN account"""
    await init_db()
    
    async with AsyncSessionLocal() as session:
        # Step 1: Create user
        user = User(
            id=999888777,
            username="flowtest",
            first_name="Flow",
            last_name="Test"
        )
        session.add(user)
        await session.flush()
        
        # Step 2: Create order
        order = Order(
            user_id=999888777,
            days=30,
            traffic_gb=40,
            price=150000,
            status="pending"
        )
        session.add(order)
        await session.flush()
        
        # Step 3: Create payment
        payment = Payment(
            order_id=order.id,
            user_id=999888777,
            receipt_file_id="receipt_123",
            status="pending"
        )
        session.add(payment)
        await session.flush()
        
        # Step 4: Approve payment and create VPN account
        payment.status = "approved"
        payment.reviewed_at = datetime.utcnow()
        payment.reviewed_by = 123456  # Admin ID
        
        order.status = "completed"
        
        vpn_account = VPNAccount(
            order_id=order.id,
            user_id=999888777,
            xui_client_id="manual",
            subscription_path="https://vpn.example.com/sub/uuid",
            expires_at=datetime.utcnow() + timedelta(days=30),
            traffic_limit_gb=40,
            is_active=True
        )
        session.add(vpn_account)
        await session.commit()
        
        # Verify complete flow
        result = await session.execute(select(Order).where(Order.id == order.id))
        final_order = result.scalar_one()
        assert final_order.status == "completed"
        
        result = await session.execute(select(Payment).where(Payment.order_id == order.id))
        final_payment = result.scalar_one()
        assert final_payment.status == "approved"
        
        result = await session.execute(select(VPNAccount).where(VPNAccount.order_id == order.id))
        final_vpn = result.scalar_one()
        assert final_vpn.is_active is True


@pytest.mark.asyncio
async def test_multiple_orders_per_user():
    """Test user can have multiple orders"""
    await init_db()
    
    async with AsyncSessionLocal() as session:
        user = User(id=888777666, username="multiorder")
        session.add(user)
        await session.flush()
        
        # Create 3 orders
        for i in range(3):
            order = Order(
                user_id=888777666,
                days=30 * (i + 1),
                traffic_gb=40 * (i + 1),
                price=100000 * (i + 1),
                status="completed"
            )
            session.add(order)
        
        await session.commit()
        
        # Verify all orders exist
        result = await session.execute(
            select(Order).where(Order.user_id == 888777666)
        )
        orders = result.scalars().all()
        
        assert len(orders) == 3


@pytest.mark.asyncio
async def test_order_rejection_flow():
    """Test order rejection flow"""
    await init_db()
    
    async with AsyncSessionLocal() as session:
        user = User(id=777666555, username="rejected")
        session.add(user)
        await session.flush()
        
        order = Order(
            user_id=777666555,
            days=30,
            traffic_gb=40,
            price=150000,
            status="pending"
        )
        session.add(order)
        await session.flush()
        
        payment = Payment(
            order_id=order.id,
            user_id=777666555,
            receipt_file_id="bad_receipt",
            status="pending"
        )
        session.add(payment)
        await session.flush()
        
        # Reject payment
        payment.status = "rejected"
        payment.reviewed_at = datetime.utcnow()
        payment.admin_note = "Invalid receipt"
        order.status = "rejected"
        
        await session.commit()
        
        # Verify rejection
        result = await session.execute(select(Payment).where(Payment.id == payment.id))
        rejected_payment = result.scalar_one()
        assert rejected_payment.status == "rejected"
        assert rejected_payment.admin_note is not None


@pytest.mark.asyncio
async def test_account_expiry_status():
    """Test checking account expiry status"""
    await init_db()
    
    async with AsyncSessionLocal() as session:
        user = User(id=666555444, username="expiry_check")
        session.add(user)
        await session.flush()
        
        order = Order(user_id=666555444, days=1, traffic_gb=10, price=50000, status="completed")
        session.add(order)
        await session.flush()
        
        # Create expired account
        expired_account = VPNAccount(
            order_id=order.id,
            user_id=666555444,
            xui_client_id="expired",
            subscription_path="https://vpn.com/expired",
            expires_at=datetime.utcnow() - timedelta(days=1),
            traffic_limit_gb=10,
            is_active=True
        )
        session.add(expired_account)
        await session.commit()
        
        # Check if expired
        now = datetime.utcnow()
        is_expired = expired_account.expires_at < now
        
        assert is_expired is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
