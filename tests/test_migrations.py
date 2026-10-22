"""Data migration: duplicate panel-backed account rows must be parked."""

import pytest
from sqlalchemy import select, text

from app.database.models import Order, User, VPNAccount
from app.database.session import AsyncSessionLocal, engine, init_db
from app.services.panel_settings import (
    get_panel_settings,
    repair_panel_password,
    set_panel_password,
)
from app.utils.encryption import encrypt


@pytest.mark.asyncio
async def test_panel_password_is_not_rewritten_on_every_read():
    """A healthy password must not trigger a re-encrypt (and a DB write)."""
    async with AsyncSessionLocal() as session:
        ps = await get_panel_settings(session)
        set_panel_password(ps, "s3cret-panel-password")
        await session.commit()

        stored = ps.panel_password
        assert repair_panel_password(ps) is False
        assert ps.panel_password == stored, "password was rewritten needlessly"

        # reading the settings again must not change the stored value
        ps2 = await get_panel_settings(session)
        assert ps2.panel_password == stored


def test_double_encrypted_password_is_repaired():
    from app.database.models import PanelSettings

    ps = PanelSettings(id=1)
    double = encrypt(encrypt("s3cret"))
    ps.panel_password = double
    assert repair_panel_password(ps) is True
    assert ps.panel_password != double
    assert ps.panel_password.startswith("gAAAA")


def test_plaintext_password_is_left_to_the_migration():
    from app.database.models import PanelSettings

    ps = PanelSettings(id=1)
    ps.panel_password = "plain-text"
    assert repair_panel_password(ps) is False
    assert ps.panel_password == "plain-text"


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
