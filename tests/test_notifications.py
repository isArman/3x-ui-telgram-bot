import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from app.utils.notifications import check_expiring_accounts
from app.database.models import User, Order, VPNAccount
from app.database.session import AsyncSessionLocal, init_db


@pytest.mark.asyncio
async def test_no_expiring_accounts():
    """Test when no accounts are expiring"""
    await init_db()
    
    bot_mock = AsyncMock()
    
    async with AsyncSessionLocal() as session:
        notified = await check_expiring_accounts(session, bot_mock)
        
        assert notified == 0
        bot_mock.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_expiring_account_notification():
    """Test notification sent for expiring account"""
    await init_db()
    
    bot_mock = AsyncMock()
    
    async with AsyncSessionLocal() as session:
        # Create test data
        user = User(id=111111, username="expiring_test")
        session.add(user)
        await session.flush()
        
        order = Order(
            user_id=111111,
            days=30,
            traffic_gb=40,
            price=150000,
            status="completed"
        )
        session.add(order)
        await session.flush()
        
        # Account expiring in 2 days
        vpn_account = VPNAccount(
            order_id=order.id,
            user_id=111111,
            config_ref="expiring_test_uuid",
            subscription_path="https://test.com/sub",
            expires_at=datetime.utcnow() + timedelta(days=2),
            traffic_limit_gb=40,
            is_active=True,
            expiry_notified=False
        )
        session.add(vpn_account)
        await session.commit()
        
        # Check notifications
        notified = await check_expiring_accounts(session, bot_mock)
        
        assert notified == 1
        bot_mock.send_message.assert_called_once()
        
        # Check that account was marked as notified
        await session.refresh(vpn_account)
        assert vpn_account.expiry_notified is True


@pytest.mark.asyncio
async def test_already_notified_account():
    """Test that already notified accounts are not notified again"""
    await init_db()
    
    bot_mock = AsyncMock()
    
    async with AsyncSessionLocal() as session:
        user = User(id=222222, username="already_notified")
        session.add(user)
        await session.flush()
        
        order = Order(
            user_id=222222,
            days=30,
            traffic_gb=40,
            price=150000,
            status="completed"
        )
        session.add(order)
        await session.flush()
        
        # Account already notified
        vpn_account = VPNAccount(
            order_id=order.id,
            user_id=222222,
            config_ref="notified_uuid",
            subscription_path="https://test.com/sub",
            expires_at=datetime.utcnow() + timedelta(days=2),
            traffic_limit_gb=40,
            is_active=True,
            expiry_notified=True  # Already notified
        )
        session.add(vpn_account)
        await session.commit()
        
        # Check notifications
        notified = await check_expiring_accounts(session, bot_mock)
        
        assert notified == 0
        bot_mock.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_expired_account_not_notified():
    """Test that already expired accounts are not notified"""
    await init_db()
    
    bot_mock = AsyncMock()
    
    async with AsyncSessionLocal() as session:
        user = User(id=333333, username="expired_user")
        session.add(user)
        await session.flush()
        
        order = Order(
            user_id=333333,
            days=30,
            traffic_gb=40,
            price=150000,
            status="completed"
        )
        session.add(order)
        await session.flush()
        
        # Already expired account
        vpn_account = VPNAccount(
            order_id=order.id,
            user_id=333333,
            config_ref="expired_uuid",
            subscription_path="https://test.com/sub",
            expires_at=datetime.utcnow() - timedelta(days=1),  # Expired
            traffic_limit_gb=40,
            is_active=True,
            expiry_notified=False
        )
        session.add(vpn_account)
        await session.commit()
        
        # Check notifications
        notified = await check_expiring_accounts(session, bot_mock)
        
        assert notified == 0
        bot_mock.send_message.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
