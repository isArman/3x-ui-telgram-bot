"""Data migration: duplicate panel-backed account rows must be parked."""

import pytest
from sqlalchemy import select, text

from app.database.models import Order, User, VPNAccount
from app.database.session import AsyncSessionLocal, engine, init_db


@pytest.mark.asyncio
async def test_init_db_parks_duplicate_auto_accounts():
    await init_db()

    async with AsyncSessionLocal() as session:
        session.add(User(id=990001, username="dupes"))
        await session.flush()
        for i in range(3):
            order = Order(
                user_id=990001, days=30, traffic_gb=10, price=1000, status="completed"
            )
            session.add(order)
            await session.flush()
            session.add(
                VPNAccount(
                    order_id=order.id,
                    user_id=990001,
                    config_ref="xui-auto",
                    subscription_path="https://sub/x",
                    expires_at=order.created_at,
                    traffic_limit_gb=10,
                    is_active=True,
                )
            )
        await session.commit()

    # re-run the migration the way a restart would
    async with engine.begin() as conn:
        from app.database.session import _migrate_sqlite_dedupe_auto_accounts

        await _migrate_sqlite_dedupe_auto_accounts(conn)

    async with AsyncSessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(VPNAccount).where(VPNAccount.user_id == 990001)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 3
        assert sum(1 for r in rows if r.is_active) == 1


@pytest.mark.asyncio
async def test_migration_leaves_manual_accounts_alone():
    await init_db()

    async with AsyncSessionLocal() as session:
        session.add(User(id=990002, username="manual_rows"))
        await session.flush()
        for i in range(2):
            order = Order(
                user_id=990002, days=30, traffic_gb=10, price=1000, status="completed"
            )
            session.add(order)
            await session.flush()
            session.add(
                VPNAccount(
                    order_id=order.id,
                    user_id=990002,
                    config_ref="manual",
                    subscription_path="https://sub/m",
                    expires_at=order.created_at,
                    traffic_limit_gb=10,
                    is_active=True,
                )
            )
        await session.commit()

    async with engine.begin() as conn:
        from app.database.session import _migrate_sqlite_dedupe_auto_accounts

        await _migrate_sqlite_dedupe_auto_accounts(conn)

    async with AsyncSessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(VPNAccount).where(VPNAccount.user_id == 990002)
                )
            )
            .scalars()
            .all()
        )
        assert all(r.is_active for r in rows), "manual deliveries must stay active"
