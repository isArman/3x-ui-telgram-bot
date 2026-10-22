"""Regression tests for the bugs found while auditing the bot.

Each test pins one behaviour that was broken before the fix.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.bot.keyboards.admin import (
    plan_admin_detail_keyboard,
    plan_admin_list_keyboard,
    plan_select_keyboard,
)
from app.bot.keyboards.user import accounts_list_keyboard, renew_plans_keyboard
from app.database.models import Order, Payment, User, VPNAccount
from app.database.session import AsyncSessionLocal
from app.services.order_fulfillment import complete_payment_approval
from app.services.xui_provisioning import ProvisionResult


# --- helpers ---------------------------------------------------------------


def _all_callback_data(markup) -> list[str]:
    return [btn.callback_data for row in markup.inline_keyboard for btn in row]


def _fake_callback(data: str, user_id: int = 1):
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id),
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=user_id),
            edit_text=AsyncMock(),
            delete=AsyncMock(),
            answer=AsyncMock(),
        ),
        bot=SimpleNamespace(send_message=AsyncMock()),
    )


# --- bug: one panel client must not create parallel account rows ----------


@pytest.mark.asyncio
async def test_repeat_purchase_keeps_single_account_row_per_user():
    """The panel keeps one client per user and extends it; the DB must follow."""
    async with AsyncSessionLocal() as session:
        user = User(id=880001, username="repeat")
        session.add(user)
        await session.flush()

        first = Order(user_id=user.id, days=30, traffic_gb=10, price=1000, status="pending")
        session.add(first)
        await session.flush()
        await complete_payment_approval(
            session,
            Payment(order_id=first.id, user_id=user.id, receipt_file_id="r1", status="pending"),
            first,
            admin_id=1,
            config_text="https://sub.example/abc",
            config_ref="xui-auto",
            provision=ProvisionResult(
                subscription_url="https://sub.example/abc",
                client_email=str(user.id),
                total_bytes=10 * 1024**3,
                expiry_ms=int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp() * 1000),
            ),
        )

        second = Order(user_id=user.id, days=30, traffic_gb=10, price=1000, status="pending")
        session.add(second)
        await session.flush()
        await complete_payment_approval(
            session,
            Payment(order_id=second.id, user_id=user.id, receipt_file_id="r2", status="pending"),
            second,
            admin_id=1,
            config_text="https://sub.example/abc",
            config_ref="xui-auto",
            provision=ProvisionResult(
                subscription_url="https://sub.example/abc",
                client_email=str(user.id),
                total_bytes=20 * 1024**3,
                expiry_ms=int((datetime.now(timezone.utc) + timedelta(days=60)).timestamp() * 1000),
            ),
        )

        rows = (
            (
                await session.execute(
                    select_accounts(user.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1, f"expected one account row, got {len(rows)}"
        # the row mirrors the panel, not the individual order
        assert rows[0].traffic_limit_gb == 20
        assert rows[0].expires_at > datetime.utcnow() + timedelta(days=50)


def select_accounts(user_id: int):
    from sqlalchemy import select

    from app.database.models import VPNAccount as _Acc

    return select(_Acc).where(_Acc.user_id == user_id)


@pytest.mark.asyncio
async def test_manual_deliveries_still_get_their_own_row():
    """Inventory/manual deliveries are separate services — one row each."""
    async with AsyncSessionLocal() as session:
        user = User(id=880002, username="manual")
        session.add(user)
        await session.flush()

        for i in range(2):
            order = Order(user_id=user.id, days=30, traffic_gb=10, price=1000, status="pending")
            session.add(order)
            await session.flush()
            await complete_payment_approval(
                session,
                Payment(order_id=order.id, user_id=user.id, receipt_file_id="r", status="pending"),
                order,
                admin_id=1,
                config_text=f"https://sub.example/manual{i}",
                config_ref="manual",
            )

        rows = (
            (await session.execute(select_accounts(user.id))).scalars().all()
        )
        assert len(rows) == 2


# --- bug: "my accounts" hid everything in manual mode ---------------------


@pytest.mark.asyncio
async def test_accounts_list_falls_back_to_stored_accounts_without_panel():
    """Manual mode has no panel — the user must still see the link they paid for."""
    from app.bot.handlers import user as user_handlers

    async with AsyncSessionLocal() as session:
        session.add(User(id=880003, username="nopanel"))
        await session.flush()
        order = Order(user_id=880003, days=30, traffic_gb=10, price=1000, status="completed")
        session.add(order)
        await session.flush()
        session.add(
            VPNAccount(
                order_id=order.id,
                user_id=880003,
                config_ref="manual",
                subscription_path="https://sub.example/stored",
                expires_at=datetime.utcnow() + timedelta(days=30),
                traffic_limit_gb=10,
                is_active=True,
            )
        )
        await session.commit()

    bot = SimpleNamespace(send_message=AsyncMock())
    event = SimpleNamespace(
        from_user=SimpleNamespace(id=880003),
        bot=bot,
        message=SimpleNamespace(chat=SimpleNamespace(id=880003)),
    )

    with patch.object(
        user_handlers,
        "get_panel_settings",
        new=AsyncMock(
            return_value=SimpleNamespace(
                panel_url=None, panel_username=None, subscription_base_url=None
            )
        ),
    ), patch.object(user_handlers, "get_panel_password", return_value=""):
        await user_handlers.send_accounts_list(event)

    bot.send_message.assert_awaited_once()
    text = bot.send_message.await_args.kwargs["text"]
    assert "https://sub.example/stored" in text
    # a renew button for the stored account must be offered
    callbacks = _all_callback_data(bot.send_message.await_args.kwargs["reply_markup"])
    assert any(cb.startswith("renew_account:") for cb in callbacks)


# --- bug: a zero-priced order crashed the wallet flow ---------------------


@pytest.mark.asyncio
async def test_free_order_is_delivered_without_wallet_debit():
    """A free plan must not raise 'debit amount must be positive'."""
    from app.bot.handlers import user as user_handlers

    async with AsyncSessionLocal() as session:
        session.add(User(id=880004, username="free"))
        await session.flush()
        order = Order(user_id=880004, days=30, traffic_gb=10, price=0, status="pending")
        session.add(order)
        await session.commit()
        order_id = order.id

    bot = SimpleNamespace(send_message=AsyncMock())
    state = SimpleNamespace(
        clear=AsyncMock(),
        get_data=AsyncMock(return_value={}),
        update_data=AsyncMock(),
        set_state=AsyncMock(),
    )

    with patch.object(
        user_handlers, "fulfill_full_wallet_order", new=AsyncMock()
    ) as mock_fulfill:
        order_stub = Order(id=order_id, user_id=880004, price=0, days=30, traffic_gb=10, plan_id=None)
        await user_handlers.deliver_free_order(bot, 880004, order_stub, state)

    mock_fulfill.assert_awaited_once()

    async with AsyncSessionLocal() as session:
        from sqlalchemy import select

        payments = (
            (
                await session.execute(
                    select(Payment).where(Payment.order_id == order_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(payments) == 1
        assert payments[0].receipt_file_id == "free"


@pytest.mark.asyncio
async def test_wallet_debit_is_never_called_with_zero():
    """debit_balance rejects amount <= 0 — the caller must not pass it."""
    from app.services.wallet import debit_balance

    async with AsyncSessionLocal() as session:
        session.add(User(id=880005, username="zerodebit"))
        await session.flush()
        with pytest.raises(ValueError):
            await debit_balance(session, 880005, 0)


# --- bug: callback payloads over 64 bytes break whole keyboards -----------


def test_plan_keyboards_fit_telegram_callback_limit():
    long_id = "p" * 50

    for markup in (
        plan_admin_list_keyboard(
            [SimpleNamespace(id=long_id, name="n", price=1, is_active=True)]
        ),
        plan_admin_detail_keyboard(long_id, True),
        plan_select_keyboard([{"id": long_id, "name": "n"}], "configs:add_plan"),
        renew_plans_keyboard([{"id": long_id, "name": "n", "price": 1}], 1),
    ):
        for data in _all_callback_data(markup):
            assert len(data.encode("utf-8")) <= 64, data


def test_plan_id_limit_keeps_every_callback_short():
    from app.services.plans_catalog import MAX_PLAN_ID_LENGTH

    plan_id = "p" * MAX_PLAN_ID_LENGTH
    for data in (
        f"admin:plans:edit:{plan_id}:description",
        f"admin:plans:toggle:{plan_id}",
        f"admin:plans:view:{plan_id}",
        f"configs:add_plan:{plan_id}",
        f"plan:{plan_id}",
        f"renew_plan:12345:{plan_id}",
    ):
        assert len(data.encode("utf-8")) <= 64, data


def test_accounts_list_keyboard_supports_stored_accounts():
    markup = accounts_list_keyboard(account_ids=[7, 9])
    callbacks = _all_callback_data(markup)
    assert "renew_account:7" in callbacks
    assert "renew_account:9" in callbacks
    assert "back:main" in callbacks


# --- bug: malformed callback data crashed the handlers --------------------


@pytest.mark.asyncio
async def test_renew_handlers_survive_malformed_callback_data():
    from app.bot.handlers import user as user_handlers

    for data in ("renew_account:not-a-number", "renew_account:", "renew_plan:abc"):
        callback = _fake_callback(data, user_id=880006)
        if data.startswith("renew_plan"):
            await user_handlers.renew_select_plan(callback, SimpleNamespace())
        else:
            await user_handlers.renew_account_start(callback, SimpleNamespace())
        callback.answer.assert_awaited()


@pytest.mark.asyncio
async def test_approve_payment_answers_callback_when_fulfillment_fails():
    """A failing fulfillment must not leave the admin with a spinning button."""
    from app.bot.handlers import admin as admin_handlers

    async with AsyncSessionLocal() as session:
        session.add(User(id=880007, username="adminuser"))
        await session.flush()
        order = Order(user_id=880007, days=30, traffic_gb=10, price=1000, status="paid")
        session.add(order)
        await session.flush()
        payment = Payment(
            order_id=order.id, user_id=880007, receipt_file_id="r", status="pending"
        )
        session.add(payment)
        await session.commit()
        payment_id = payment.id

    callback = _fake_callback(f"approve_payment:{payment_id}", user_id=880007)
    state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock())

    with patch.object(
        admin_handlers,
        "fulfill_paid_order",
        new=AsyncMock(side_effect=RuntimeError("panel exploded")),
    ), patch.object(
        admin_handlers, "get_plan", new=AsyncMock(return_value=None)
    ), patch.object(
        admin_handlers, "is_admin", return_value=True
    ):
        await admin_handlers.approve_payment(callback, state)

    callback.answer.assert_awaited()
    assert callback.answer.await_args.kwargs.get("show_alert") is True

    async with AsyncSessionLocal() as session:
        from sqlalchemy import select

        payment = (
            (
                await session.execute(
                    select(Payment).where(Payment.id == payment_id)
                )
            )
            .scalar_one()
        )
        assert payment.status == "pending", "a failed approval must stay reviewable"
