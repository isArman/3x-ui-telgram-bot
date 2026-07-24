from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config.settings import settings

from .base import Base


engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True,
)

if settings.DATABASE_URL.startswith("sqlite"):

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def _migrate_sqlite_columns(conn):
    result = await conn.execute(text("PRAGMA table_info(users)"))
    user_cols = {row[1] for row in result.fetchall()}
    if "balance" not in user_cols:
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN balance INTEGER DEFAULT 0")
        )
    if "referral_code" not in user_cols:
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN referral_code VARCHAR(32)")
        )
    if "referred_by_user_id" not in user_cols:
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN referred_by_user_id BIGINT")
        )

    result = await conn.execute(text("PRAGMA table_info(orders)"))
    order_cols = {row[1] for row in result.fetchall()}
    if "plan_id" not in order_cols:
        await conn.execute(text("ALTER TABLE orders ADD COLUMN plan_id VARCHAR(50)"))
    if "renew_vpn_account_id" not in order_cols:
        await conn.execute(
            text("ALTER TABLE orders ADD COLUMN renew_vpn_account_id INTEGER")
        )
    if "wallet_debit" not in order_cols:
        await conn.execute(
            text("ALTER TABLE orders ADD COLUMN wallet_debit INTEGER DEFAULT 0")
        )
    if "original_price" not in order_cols:
        await conn.execute(text("ALTER TABLE orders ADD COLUMN original_price INTEGER"))
    if "referral_discount_applied" not in order_cols:
        await conn.execute(
            text(
                "ALTER TABLE orders ADD COLUMN referral_discount_applied "
                "BOOLEAN DEFAULT 0"
            )
        )
    if "referral_cashback_paid" not in order_cols:
        await conn.execute(
            text(
                "ALTER TABLE orders ADD COLUMN referral_cashback_paid "
                "BOOLEAN DEFAULT 0"
            )
        )

    result = await conn.execute(text("PRAGMA table_info(vpn_accounts)"))
    vpn_cols = {row[1] for row in result.fetchall()}
    if "config_ref" not in vpn_cols and "xui_client_id" in vpn_cols:
        await conn.execute(
            text("ALTER TABLE vpn_accounts RENAME COLUMN xui_client_id TO config_ref")
        )
    elif "config_ref" not in vpn_cols:
        await conn.execute(
            text(
                "ALTER TABLE vpn_accounts "
                "ADD COLUMN config_ref VARCHAR(255) DEFAULT 'manual'"
            )
        )
    if "traffic_low_notified" not in vpn_cols:
        await conn.execute(
            text(
                "ALTER TABLE vpn_accounts "
                "ADD COLUMN traffic_low_notified BOOLEAN DEFAULT 0"
            )
        )


async def _backfill_referral_codes(conn):
    """Assign referral codes to users that still lack one."""
    result = await conn.execute(
        text("SELECT id FROM users WHERE referral_code IS NULL OR referral_code = ''")
    )
    rows = result.fetchall()
    if not rows:
        return

    # Import here to avoid circular imports at module load
    from app.services.referral import generate_referral_code

    existing = await conn.execute(
        text("SELECT referral_code FROM users WHERE referral_code IS NOT NULL")
    )
    used = {row[0] for row in existing.fetchall() if row[0]}

    for (user_id,) in rows:
        code = generate_referral_code(user_id)
        # Ensure uniqueness within this backfill pass
        base = code
        n = 0
        while code in used:
            n += 1
            code = f"{base}{n}"
        used.add(code)
        await conn.execute(
            text("UPDATE users SET referral_code = :code WHERE id = :uid"),
            {"code": code, "uid": user_id},
        )


async def _migrate_sqlite_indexes(conn):
    """Add indexes that create_all won't retrofit onto existing SQLite tables."""
    # Keep one pending payment per order before enforcing uniqueness
    await conn.execute(
        text(
            """
            DELETE FROM payments
            WHERE status = 'pending'
              AND id NOT IN (
                  SELECT MIN(id) FROM payments
                  WHERE status = 'pending'
                  GROUP BY order_id
              )
            """
        )
    )

    statements = [
        "CREATE INDEX IF NOT EXISTS ix_orders_user_id ON orders(user_id)",
        "CREATE INDEX IF NOT EXISTS ix_payments_order_id ON payments(order_id)",
        "CREATE INDEX IF NOT EXISTS ix_payments_user_id ON payments(user_id)",
        "CREATE INDEX IF NOT EXISTS ix_vpn_accounts_user_id ON vpn_accounts(user_id)",
        "CREATE INDEX IF NOT EXISTS ix_plan_configs_plan_id ON plan_configs(plan_id)",
        "CREATE INDEX IF NOT EXISTS ix_plan_configs_order_id ON plan_configs(order_id)",
        "CREATE INDEX IF NOT EXISTS ix_wallet_topups_user_id ON wallet_topups(user_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_referral_code "
        "ON users(referral_code) WHERE referral_code IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS ix_users_referred_by_user_id "
        "ON users(referred_by_user_id)",
        # At most one pending payment per order
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_payments_pending_order "
        "ON payments(order_id) WHERE status = 'pending'",
    ]
    for stmt in statements:
        await conn.execute(text(stmt))


async def init_db():
    """Initialize database tables and seed shop settings if empty."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if settings.DATABASE_URL.startswith("sqlite"):
            await _migrate_sqlite_columns(conn)
            await _migrate_sqlite_indexes(conn)
            await _backfill_referral_codes(conn)

    # Seed card + plans outside the DDL transaction
    from app.services.bot_settings import get_bot_settings
    from app.services.plans_catalog import bootstrap_plans_from_yaml

    async with AsyncSessionLocal() as session:
        await get_bot_settings(session)
        inserted = await bootstrap_plans_from_yaml(session)
        if inserted:
            import logging

            logging.getLogger(__name__).info(
                "Bootstrapped %s shop plan(s) from YAML", inserted
            )
