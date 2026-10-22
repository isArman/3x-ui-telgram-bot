"""Smoke tests: the app must import, wire up and answer to every registered handler."""

import ast
import pathlib
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, Message, Update, User

from app.bot.handlers import admin, panel_admin, shop_admin, user, wallet
from app.core.runner import main as runner_main

_ROUTERS = (
    panel_admin.router,
    shop_admin.router,
    admin.router,
    wallet.router,
    user.router,
)


def _build_dispatcher() -> Dispatcher:
    # The handler routers are module-level singletons and aiogram refuses to
    # attach a router to two dispatchers, so build it once and share it.
    global _DISPATCHER
    if _DISPATCHER is None:
        dp = Dispatcher(storage=MemoryStorage())
        for router in _ROUTERS:
            dp.include_router(router)
        _DISPATCHER = dp
    return _DISPATCHER


_DISPATCHER: Dispatcher | None = None


def test_dispatcher_builds_with_every_router():
    dp = _build_dispatcher()

    for router in _ROUTERS:
        assert router.message.handlers, router
        assert router.callback_query.handlers, router

    assert set(dp.resolve_used_update_types()) == {
        "message",
        "callback_query",
        "pre_checkout_query",
    }
    assert runner_main is not None


def test_every_router_is_included_in_the_runner():
    src = pathlib.Path("app/core/runner.py").read_text()
    for module in ("panel_admin", "shop_admin", "admin", "wallet", "user"):
        assert f"include_router({module}.router)" in src, module


def test_no_bare_except_in_handlers():
    """`except:` swallows Ctrl-C and real bugs — it must not be used."""
    offenders = []
    for path in pathlib.Path("app").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                offenders.append(f"{path}:{node.lineno}")
    assert not offenders, f"bare except in: {offenders}"


@pytest.mark.parametrize(
    "module",
    ["admin", "panel_admin", "shop_admin", "user", "wallet"],
)
def test_handler_modules_import_cleanly(module):
    __import__(f"app.bot.handlers.{module}")


@pytest.mark.asyncio
async def test_start_command_is_routed_end_to_end():
    """A real update must travel dispatcher -> router -> handler and run."""
    from sqlalchemy import select

    from app.database.models import User as DBUser
    from app.database.session import AsyncSessionLocal

    dp = _build_dispatcher()

    bot = AsyncMock()
    bot.get_me = AsyncMock(
        return_value=SimpleNamespace(username="test_bot", id=1, is_bot=True)
    )

    update = Update(
        update_id=1,
        message=Message(
            message_id=1,
            date=datetime(2026, 1, 1),
            chat=Chat(id=555, type="private"),
            from_user=User(id=555, is_bot=False, first_name="Tester"),
            text="/start",
        ),
    )

    await dp.feed_update(bot, update)

    # The handler ran: it created the user and its referral code.
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(select(DBUser).where(DBUser.id == 555))
        ).scalar_one_or_none()
        assert row is not None
        assert row.referral_code
