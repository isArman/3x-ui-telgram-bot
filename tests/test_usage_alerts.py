"""Expiry and low-traffic alerts point the customer at the Mini App."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.database.models import Order, User, VPNAccount
from app.database.session import AsyncSessionLocal, init_db
from app.services.renewal import extend_vpn_account
from app.services.traffic_usage import GB
from app.utils.notifications import EXPIRY_WARNING_DAYS, check_expiring_accounts


@pytest.mark.asyncio
async def test_expiry_alert_is_sent_once():
    await init_db()
    bot_mock = AsyncMock()

    async with AsyncSessionLocal() as session:
        session.add(User(id=880101, username="expiring_test"))
        await session.flush()
        order = Order(
            user_id=880101,
            days=30,
            traffic_gb=40,
            price=150000,
            status="completed",
        )
        session.add(order)
        await session.flush()
        vpn_account = VPNAccount(
            order_id=order.id,
            user_id=880101,
            config_ref="xui-auto",
            subscription_path="https://test.com/sub",
            expires_at=datetime.utcnow() + timedelta(days=2),
            traffic_limit_gb=40,
            is_active=True,
            expiry_notified=False,
        )
        session.add(vpn_account)
        await session.commit()

        assert await check_expiring_accounts(session, bot_mock) == 1
        bot_mock.send_message.assert_called_once()

        # the alert points at the Mini App, where the renewal lives
        text = bot_mock.send_message.call_args.kwargs["text"]
        assert "Mini App" in text

        await session.refresh(vpn_account)
        assert vpn_account.expiry_notified is True

        # a second run does not re-alert
        assert await check_expiring_accounts(session, bot_mock) == 0


@pytest.mark.asyncio
async def test_alert_window_is_three_days():
    await init_db()
    bot_mock = AsyncMock()

    async with AsyncSessionLocal() as session:
        session.add(User(id=880102, username="far_user"))
        await session.flush()
        order = Order(
            user_id=880102,
            days=30,
            traffic_gb=40,
            price=150000,
            status="completed",
        )
        session.add(order)
        await session.flush()
        session.add(
            VPNAccount(
                order_id=order.id,
                user_id=880102,
                config_ref="xui-auto",
                subscription_path="https://test.com/sub",
                expires_at=datetime.utcnow() + timedelta(days=EXPIRY_WARNING_DAYS + 5),
                traffic_limit_gb=40,
                is_active=True,
                expiry_notified=False,
            )
        )
        await session.commit()

        assert await check_expiring_accounts(session, bot_mock) == 0
        bot_mock.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_renewal_resets_alert_flags():
    """After a renewal the account can be alerted again next cycle."""
    await init_db()

    async with AsyncSessionLocal() as session:
        session.add(User(id=880103, username="renew_user"))
        await session.flush()
        order = Order(
            user_id=880103,
            days=30,
            traffic_gb=10,
            price=100000,
            status="completed",
        )
        session.add(order)
        await session.flush()
        account = VPNAccount(
            order_id=order.id,
            user_id=880103,
            config_ref="xui-auto",
            subscription_path="https://test.com/sub",
            expires_at=datetime.utcnow() + timedelta(days=1),
            traffic_limit_gb=10,
            is_active=True,
            expiry_notified=True,
            traffic_low_notified=True,
        )
        session.add(account)
        await session.commit()

        renew_order = Order(
            user_id=880103,
            days=30,
            traffic_gb=20,
            price=100000,
            renew_vpn_account_id=account.id,
            status="pending",
        )
        session.add(renew_order)
        await session.flush()

        old_expiry = account.expires_at
        await extend_vpn_account(session, account, renew_order)
        await session.commit()
        await session.refresh(account)

        assert account.expiry_notified is False
        assert account.traffic_low_notified is False
        assert account.expires_at > old_expiry
        assert account.traffic_limit_gb == 30


@pytest.mark.asyncio
@patch("app.utils.notifications.is_auto_provisioning_ready", return_value=True)
@patch("app.utils.notifications.xui_client_for_panel")
async def test_low_traffic_alert_uses_the_panel_list(mock_xui_ctx, _mock_ready):
    await init_db()
    bot_mock = AsyncMock()
    panel_client = AsyncMock()
    panel_client.list_clients.return_value = [
        {
            "email": "880104",
            "totalGB": 10 * GB,
            "traffic": {"up": int(9.1 * GB), "down": 0},
        }
    ]

    class _Ctx:
        async def __aenter__(self):
            return panel_client

        async def __aexit__(self, *a):
            return False

    mock_xui_ctx.return_value = _Ctx()

    from app.utils.notifications import check_low_traffic_accounts

    async with AsyncSessionLocal() as session:
        session.add(User(id=880104, username="low_user"))
        await session.flush()
        order = Order(
            user_id=880104, days=30, traffic_gb=10, price=100000, status="completed"
        )
        session.add(order)
        await session.flush()
        account = VPNAccount(
            order_id=order.id,
            user_id=880104,
            config_ref="xui-auto",
            subscription_path="https://test.com/sub",
            expires_at=datetime.utcnow() + timedelta(days=30),
            traffic_limit_gb=10,
            is_active=True,
            traffic_low_notified=False,
        )
        session.add(account)
        await session.commit()

        notified = await check_low_traffic_accounts(session, bot_mock)
        assert notified == 1
        text = bot_mock.send_message.call_args.kwargs["text"]
        assert "Mini App" in text
