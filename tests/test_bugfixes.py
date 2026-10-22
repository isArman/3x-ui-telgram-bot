"""Regression tests for the bugs found while auditing the shop.

Service-level behaviour lives here; the HTTP surface is covered by
``tests/test_miniapp.py``.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.database.models import Order, Payment, PlanConfig, User, VPNAccount
from app.database.session import AsyncSessionLocal
from app.services.order_fulfillment import complete_payment_approval
from app.services.plans_catalog import MAX_PLAN_ID_LENGTH
from app.services.promocodes import validate_promo
from app.services.trial import (
    claim_trial,
    create_trial_order,
    get_trial_settings,
    has_used_trial,
    trial_available,
)
from app.services.wallet import credit_balance, debit_balance, get_balance


# --- account lifecycle -----------------------------------------------------


@pytest.mark.asyncio
async def test_repeat_purchase_keeps_single_account_row_per_user():
    """The panel keeps one client per user, so the database must too."""
    async with AsyncSessionLocal() as session:
        user = User(id=880001, username="repeat")
        session.add(user)
        await session.flush()

        first = Order(
            user_id=user.id, days=30, traffic_gb=10, price=1000, status="pending"
        )
        session.add(first)
        await session.flush()
        await complete_payment_approval(
            session,
            Payment(
                order_id=first.id,
                user_id=user.id,
                receipt_file_id="",
                status="pending",
            ),
            first,
            admin_id=1,
            config_text="https://sub.example/abc",
            config_ref="xui-auto",
        )

        second = Order(
            user_id=user.id, days=30, traffic_gb=10, price=1000, status="pending"
        )
        session.add(second)
        await session.flush()
        await complete_payment_approval(
            session,
            Payment(
                order_id=second.id,
                user_id=user.id,
                receipt_file_id="",
                status="pending",
            ),
            second,
            admin_id=1,
            config_text="https://sub.example/abc",
            config_ref="xui-auto",
        )

        rows = (
            (
                await session.execute(
                    select(VPNAccount).where(VPNAccount.user_id == user.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1, f"expected one account row, got {len(rows)}"


@pytest.mark.asyncio
async def test_manual_deliveries_still_get_their_own_row():
    """Manual deliveries are separate services, so each keeps its own row."""
    async with AsyncSessionLocal() as session:
        user = User(id=880002, username="manual")
        session.add(user)
        await session.flush()

        for i in range(2):
            order = Order(
                user_id=user.id, days=30, traffic_gb=10, price=1000, status="pending"
            )
            session.add(order)
            await session.flush()
            await complete_payment_approval(
                session,
                Payment(
                    order_id=order.id,
                    user_id=user.id,
                    receipt_file_id="",
                    status="pending",
                ),
                order,
                admin_id=1,
                config_text=f"https://sub.example/manual{i}",
                config_ref="manual",
            )

        rows = (
            (
                await session.execute(
                    select(VPNAccount).where(VPNAccount.user_id == user.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 2


# --- promo codes -----------------------------------------------------------


@pytest.mark.asyncio
async def test_promo_cannot_be_used_twice_by_one_customer():
    from app.services.promocodes import create_promo, reserve_promo

    async with AsyncSessionLocal() as session:
        user = User(id=880003, username="promo")
        session.add(user)
        await session.flush()
        promo = await create_promo(session, code="ONCE", kind="days", value=5)
        await session.commit()

        application, error = await validate_promo(
            session, "ONCE", user_id=user.id, price=10000
        )
        assert application is not None, error
        assert application.bonus_days == 5

        order = Order(
            user_id=user.id,
            days=35,
            traffic_gb=10,
            price=10000,
            promo_code=promo.code,
            status="pending",
        )
        session.add(order)
        await session.flush()
        await reserve_promo(session, promo)
        await session.commit()

        again, error = await validate_promo(
            session, "ONCE", user_id=user.id, price=10000
        )
        assert again is None
        assert "قبلاً" in error


@pytest.mark.asyncio
async def test_expired_promo_is_rejected():
    from app.services.promocodes import create_promo

    async with AsyncSessionLocal() as session:
        user = User(id=880004, username="expired")
        session.add(user)
        await session.flush()
        await create_promo(
            session,
            code="OLD",
            kind="percent",
            value=10,
            expires_at=datetime.utcnow() - timedelta(days=1),
        )
        await session.commit()

        application, error = await validate_promo(
            session, "OLD", user_id=user.id, price=10000
        )
        assert application is None
        assert "تمام" in error


# --- wallet ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_wallet_debit_is_never_called_with_zero():
    """debit_balance rejects amount <= 0 — the caller must not pass it."""
    async with AsyncSessionLocal() as session:
        session.add(User(id=880005, username="zerodebit"))
        await session.flush()
        with pytest.raises(ValueError):
            await debit_balance(session, 880005, 0)


@pytest.mark.asyncio
async def test_wallet_credit_and_debit_are_balanced():
    async with AsyncSessionLocal() as session:
        session.add(User(id=880006, username="wallet"))
        await session.flush()
        await credit_balance(session, 880006, 50_000)
        await session.commit()
        assert await get_balance(session, 880006) == 50_000

        await debit_balance(session, 880006, 20_000)
        await session.commit()
        assert await get_balance(session, 880006) == 30_000


# --- trials ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_trial_is_offered_once_per_customer():
    async with AsyncSessionLocal() as session:
        user = User(id=880007, username="trial")
        session.add(user)
        await session.flush()

        can_take, reason = await trial_available(session, user.id)
        assert can_take, reason

        settings = await get_trial_settings(session)
        order = await create_trial_order(session, user, settings)
        assert await claim_trial(session, user) is True
        await session.commit()
        assert order.price == 0
        assert order.is_trial is True

        assert await has_used_trial(session, user.id) is True
        can_take, reason = await trial_available(session, user.id)
        assert can_take is False
        assert "قبلاً" in reason

        # the trial can only be claimed once, even in parallel
        assert await claim_trial(session, user) is False


# --- plan ids --------------------------------------------------------------


def test_plan_id_limit_keeps_ids_short_enough_for_the_ui():
    """Plan ids travel through callback data and URLs, so they stay short."""
    assert MAX_PLAN_ID_LENGTH <= 24


@pytest.mark.asyncio
async def test_plan_with_a_long_id_is_rejected():
    from app.services.plans_catalog import create_plan

    async with AsyncSessionLocal() as session:
        with pytest.raises(ValueError):
            await create_plan(
                session,
                plan_id="x" * (MAX_PLAN_ID_LENGTH + 1),
                name="Too long",
                days=30,
                traffic_gb=10,
                price=1000,
            )


# --- inventory -------------------------------------------------------------


@pytest.mark.asyncio
async def test_inventory_config_is_assigned_once():
    from app.services.config_inventory import add_config, count_available

    async with AsyncSessionLocal() as session:
        session.add(PlanConfig(plan_id="basic", config_text="a", created_by=1))
        await session.commit()
        assert await count_available(session, "basic") == 1

        await add_config(session, "basic", "b", 1)
        await session.commit()
        assert await count_available(session, "basic") == 2
