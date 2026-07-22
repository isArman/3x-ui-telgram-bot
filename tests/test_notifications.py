import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch
from app.utils.notifications import check_expiring_accounts, check_low_traffic_accounts
from app.database.models import User, Order, VPNAccount
from app.database.session import AsyncSessionLocal, init_db
from app.services.traffic_usage import GB


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


class _FakeXuiContext:
    def __init__(self, client):
        self._client = client

    async def __aenter__(self):
        return self._client

    async def __aexit__(self, *args):
        return False


@pytest.mark.asyncio
@patch("app.utils.notifications.is_auto_provisioning_ready", return_value=True)
@patch("app.utils.notifications.xui_client_for_panel")
async def test_low_traffic_notification(mock_xui_ctx, _mock_ready):
    await init_db()
    bot_mock = AsyncMock()
    panel_client = AsyncMock()
    panel_client.get_client.return_value = {
        "client": {"totalGB": 10 * GB},
        "traffic": {"up": int(9.2 * GB), "down": 0},
    }
    mock_xui_ctx.return_value = _FakeXuiContext(panel_client)

    async with AsyncSessionLocal() as session:
        user = User(id=444444, username="low_traffic")
        session.add(user)
        await session.flush()

        order = Order(
            user_id=444444,
            days=30,
            traffic_gb=10,
            price=100000,
            status="completed",
        )
        session.add(order)
        await session.flush()

        vpn_account = VPNAccount(
            order_id=order.id,
            user_id=444444,
            config_ref="xui-auto",
            subscription_path="https://test.com/sub",
            expires_at=datetime.utcnow() + timedelta(days=30),
            traffic_limit_gb=10,
            is_active=True,
            traffic_low_notified=False,
        )
        session.add(vpn_account)
        await session.commit()

        notified = await check_low_traffic_accounts(session, bot_mock)

        assert notified == 1
        bot_mock.send_message.assert_called_once()
        await session.refresh(vpn_account)
        assert vpn_account.traffic_low_notified is True


@pytest.mark.asyncio
@patch("app.utils.notifications.is_auto_provisioning_ready", return_value=True)
@patch("app.utils.notifications.xui_client_for_panel")
async def test_low_traffic_skips_when_enough_remaining(mock_xui_ctx, _mock_ready):
    await init_db()
    bot_mock = AsyncMock()
    panel_client = AsyncMock()
    panel_client.get_client.return_value = {
        "client": {"totalGB": 10 * GB},
        "traffic": {"up": 5 * GB, "down": 0},
    }
    mock_xui_ctx.return_value = _FakeXuiContext(panel_client)

    async with AsyncSessionLocal() as session:
        user = User(id=555555, username="enough_traffic")
        session.add(user)
        await session.flush()

        order = Order(
            user_id=555555,
            days=30,
            traffic_gb=10,
            price=100000,
            status="completed",
        )
        session.add(order)
        await session.flush()

        vpn_account = VPNAccount(
            order_id=order.id,
            user_id=555555,
            config_ref="xui-auto",
            subscription_path="https://test.com/sub",
            expires_at=datetime.utcnow() + timedelta(days=30),
            traffic_limit_gb=10,
            is_active=True,
            traffic_low_notified=False,
        )
        session.add(vpn_account)
        await session.commit()

        notified = await check_low_traffic_accounts(session, bot_mock)

        assert notified == 0
        bot_mock.send_message.assert_not_called()


@pytest.mark.asyncio
@patch("app.utils.notifications.is_auto_provisioning_ready", return_value=False)
async def test_low_traffic_skips_without_panel(_mock_ready):
    await init_db()
    bot_mock = AsyncMock()

    async with AsyncSessionLocal() as session:
        notified = await check_low_traffic_accounts(session, bot_mock)
        assert notified == 0
        bot_mock.send_message.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
