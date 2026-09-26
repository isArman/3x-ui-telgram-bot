"""Actionable usage/expiry alerts and renew-from-alert flow."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.bot.keyboards.user import usage_alert_keyboard
from app.database.models import Order, User, VPNAccount
from app.database.session import AsyncSessionLocal, init_db
from app.services.renewal import extend_vpn_account
from app.utils.notifications import EXPIRY_WARNING_DAYS, check_expiring_accounts


def _renew_callback_of(kb) -> str:
    return kb.inline_keyboard[0][0].callback_data


def test_alert_keyboard_wires_to_renew_flow():
    kb = usage_alert_keyboard(77)
    assert _renew_callback_of(kb) == "renew_account:77"
    assert kb.inline_keyboard[0][0].text == "تمدید اکانت"


@pytest.mark.asyncio
async def test_expiry_alert_is_actionable_and_once():
    await init_db()
    bot_mock = AsyncMock()

    async with AsyncSessionLocal() as session:
        session.add(User(id=700700, username="alert_user"))
        await session.flush()
        order = Order(
            user_id=700700, days=30, traffic_gb=10, price=100000, status="completed"
        )
        session.add(order)
        await session.flush()
        account = VPNAccount(
            order_id=order.id,
            user_id=700700,
            config_ref="xui-auto",
            subscription_path="https://test.com/sub",
            expires_at=datetime.utcnow() + timedelta(days=1),
            traffic_limit_gb=10,
            is_active=True,
            expiry_notified=False,
        )
        session.add(account)
        await session.commit()
        account_id = account.id

        notified = await check_expiring_accounts(session, bot_mock)
        assert notified == 1

        # the alert carries a working renew button for exactly this account
        _, kwargs = bot_mock.send_message.call_args
        kb = kwargs["reply_markup"]
        assert _renew_callback_of(kb) == f"renew_account:{account_id}"

        # second run does not re-alert
        assert await check_expiring_accounts(session, bot_mock) == 0


@pytest.mark.asyncio
async def test_alert_window_is_three_days():
    await init_db()
    bot_mock = AsyncMock()

    async with AsyncSessionLocal() as session:
        session.add(User(id=700701, username="far_user"))
        await session.flush()
        order = Order(
            user_id=700701, days=30, traffic_gb=10, price=100000, status="completed"
        )
        session.add(order)
        await session.flush()
        # well outside the window -> no alert
        session.add(
            VPNAccount(
                order_id=order.id,
                user_id=700701,
                config_ref="xui-auto",
                subscription_path="https://test.com/sub",
                expires_at=datetime.utcnow() + timedelta(days=EXPIRY_WARNING_DAYS + 5),
                traffic_limit_gb=10,
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
        session.add(User(id=700702, username="renew_user"))
        await session.flush()
        order = Order(
            user_id=700702, days=30, traffic_gb=10, price=100000, status="completed"
        )
        session.add(order)
        await session.flush()
        account = VPNAccount(
            order_id=order.id,
            user_id=700702,
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
            user_id=700702,
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
async def test_low_traffic_alert_is_actionable(mock_xui_ctx, _mock_ready):
    await init_db()
    bot_mock = AsyncMock()
    panel_client = AsyncMock()
    panel_client.get_client.return_value = {
        "client": {"totalGB": 10 * 1024**3},
        "traffic": {"up": int(9.5 * 1024**3), "down": 0},
    }

    class _Ctx:
        async def __aenter__(self):
            return panel_client

        async def __aexit__(self, *a):
            return False

    mock_xui_ctx.return_value = _Ctx()

    from app.utils.notifications import check_low_traffic_accounts

    async with AsyncSessionLocal() as session:
        session.add(User(id=700703, username="low_user"))
        await session.flush()
        order = Order(
            user_id=700703, days=30, traffic_gb=10, price=100000, status="completed"
        )
        session.add(order)
        await session.flush()
        account = VPNAccount(
            order_id=order.id,
            user_id=700703,
            config_ref="xui-auto",
            subscription_path="https://test.com/sub",
            expires_at=datetime.utcnow() + timedelta(days=30),
            traffic_limit_gb=10,
            is_active=True,
            traffic_low_notified=False,
        )
        session.add(account)
        await session.commit()
        account_id = account.id

        notified = await check_low_traffic_accounts(session, bot_mock)
        assert notified == 1
        _, kwargs = bot_mock.send_message.call_args
        assert _renew_callback_of(kwargs["reply_markup"]) == f"renew_account:{account_id}"
