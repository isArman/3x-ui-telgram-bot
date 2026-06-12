from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.config.settings import settings
from .base import Base


engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)


async def _migrate_sqlite_columns(conn):
    result = await conn.execute(text("PRAGMA table_info(orders)"))
    order_cols = {row[1] for row in result.fetchall()}
    if "plan_id" not in order_cols:
        await conn.execute(text("ALTER TABLE orders ADD COLUMN plan_id VARCHAR(50)"))

    result = await conn.execute(text("PRAGMA table_info(vpn_accounts)"))
    vpn_cols = {row[1] for row in result.fetchall()}
    if "config_ref" not in vpn_cols and "xui_client_id" in vpn_cols:
        await conn.execute(text("ALTER TABLE vpn_accounts RENAME COLUMN xui_client_id TO config_ref"))
    elif "config_ref" not in vpn_cols:
        await conn.execute(text("ALTER TABLE vpn_accounts ADD COLUMN config_ref VARCHAR(255) DEFAULT 'manual'"))


async def init_db():
    """Initialize database tables"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if settings.DATABASE_URL.startswith("sqlite"):
            await _migrate_sqlite_columns(conn)
