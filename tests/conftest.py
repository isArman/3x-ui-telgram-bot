import os

# Isolated in-memory DB for tests (must run before app.database.session import).
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-encryption-only")
os.environ.setdefault("BOT_TOKEN", "test:1234567890")

import pytest
from sqlalchemy import text

from app.database.base import Base
from app.database.session import engine, init_db


@pytest.fixture(autouse=True)
async def fresh_db():
    """Reset schema and clear rows between tests."""
    await init_db()
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(text(f"DELETE FROM {table.name}"))
    yield
