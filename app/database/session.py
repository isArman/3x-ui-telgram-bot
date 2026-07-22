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
    result = await conn.execute(text("PRAGMA table_info(orders)"))
    order_cols = {row[1] for row in result.fetchall()}
    if "plan_id" not in order_cols:
        await conn.execute(text("ALTER TABLE orders ADD COLUMN plan_id VARCHAR(50)"))

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
        # At most one pending payment per order
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_payments_pending_order "
        "ON payments(order_id) WHERE status = 'pending'",
    ]
    for stmt in statements:
        await conn.execute(text(stmt))


async def init_db():
    """Initialize database tables"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if settings.DATABASE_URL.startswith("sqlite"):
            await _migrate_sqlite_columns(conn)
            await _migrate_sqlite_indexes(conn)
