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
    existing = {row[1] for row in result.fetchall()}
    if "plan_id" not in existing:
        await conn.execute(text("ALTER TABLE orders ADD COLUMN plan_id VARCHAR(50)"))


async def init_db():
    """Initialize database tables"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if settings.DATABASE_URL.startswith("sqlite"):
            await _migrate_sqlite_columns(conn)
