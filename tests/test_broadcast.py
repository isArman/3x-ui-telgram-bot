"""Tests for the admin broadcast service."""

from unittest.mock import AsyncMock, patch

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from app.database.models import User
from app.database.session import AsyncSessionLocal, init_db
from app.services.broadcast import (
    BroadcastResult,
    broadcast_message,
    list_recipient_ids,
)


@pytest.mark.asyncio
async def test_list_recipient_ids_returns_all():
    await init_db()
    async with AsyncSessionLocal() as session:
        for uid in (111, 222, 333):
            session.add(User(id=uid, username=f"u{uid}"))
        await session.commit()

        ids = await list_recipient_ids(session)
        assert sorted(ids) == [111, 222, 333]


@pytest.mark.asyncio
async def test_broadcast_sends_to_all_and_counts():
    await init_db()
    bot = AsyncMock()

    async with AsyncSessionLocal() as session:
        for uid in (111, 222, 333):
            session.add(User(id=uid, username=f"u{uid}"))
        await session.commit()

        with patch("app.services.broadcast._SEND_INTERVAL_SECONDS", 0):
            result = await broadcast_message(bot, session, "hello")

    assert isinstance(result, BroadcastResult)
    assert result.total == 3
    assert result.sent == 3
    assert result.failed == 0
    assert bot.send_message.await_count == 3


@pytest.mark.asyncio
async def test_broadcast_tolerates_blocked_recipient():
    await init_db()
    bot = AsyncMock()

    async def _send(chat_id, text):
        if chat_id == 222:
            raise TelegramForbiddenError(method=None, message="blocked")

    bot.send_message.side_effect = _send

    async with AsyncSessionLocal() as session:
        for uid in (111, 222, 333):
            session.add(User(id=uid, username=f"u{uid}"))
        await session.commit()

        with patch("app.services.broadcast._SEND_INTERVAL_SECONDS", 0):
            result = await broadcast_message(bot, session, "hi")

    assert result.total == 3
    assert result.sent == 2
    assert result.failed == 1
    assert result.failed_ids == [222]


@pytest.mark.asyncio
async def test_broadcast_retries_after_rate_limit():
    await init_db()
    bot = AsyncMock()
    calls = {"n": 0}

    async def _send(chat_id, text):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TelegramRetryAfter(method=None, message="slow", retry_after=0)

    bot.send_message.side_effect = _send

    async with AsyncSessionLocal() as session:
        session.add(User(id=111, username="u"))
        await session.commit()

        with patch("app.services.broadcast._SEND_INTERVAL_SECONDS", 0):
            result = await broadcast_message(bot, session, "hi")

    assert result.sent == 1
    assert result.failed == 0
