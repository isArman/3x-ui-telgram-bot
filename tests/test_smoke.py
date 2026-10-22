"""Smoke tests: the app wires up, imports and answers a real update."""

import ast
import pathlib
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, Update, User

from app.bot import launcher, payments
from app.core.runner import main as runner_main

_ROUTERS = (payments.router, launcher.router)

_DISPATCHER: Dispatcher | None = None


def _build_dispatcher() -> Dispatcher:
    """The handler routers are module-level singletons, so build it once."""
    global _DISPATCHER
    if _DISPATCHER is None:
        dp = Dispatcher(storage=MemoryStorage())
        for router in _ROUTERS:
            dp.include_router(router)
        _DISPATCHER = dp
    return _DISPATCHER


def test_dispatcher_builds_with_every_router():
    dp = _build_dispatcher()

    for router in _ROUTERS:
        assert router.message.handlers, router

    assert set(dp.resolve_used_update_types()) == {
        "message",
        "callback_query",
        "pre_checkout_query",
    }


def test_every_router_is_included_in_the_runner():
    src = pathlib.Path("app/core/runner.py").read_text()
    for module in ("launcher", "payments"):
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


def test_no_unused_imports_in_the_app():
    """Every import in the app must be used — no leftovers from the chat UI."""
    problems = []
    for path in sorted(pathlib.Path("app").rglob("*.py")):
        source = path.read_text()
        tree = ast.parse(source)
        imported: dict[str, int] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.asname or alias.name.split(".")[0]
                    imported[name] = node.lineno
            elif isinstance(node, ast.ImportFrom):
                if node.module == "__future__":
                    continue
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    imported[alias.asname or alias.name] = node.lineno
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                base = node
                while isinstance(base, ast.Attribute):
                    base = base.value
                if isinstance(base, ast.Name):
                    used.add(base.id)
        # names used in annotations as strings
        for name, lineno in imported.items():
            if name in used:
                continue
            if name.startswith("_"):
                # imported for its side effect
                continue
            if f'"{name}' in source or f"'{name}" in source:
                continue
            problems.append(f"{path}:{lineno} {name}")
    assert not problems, f"unused imports: {problems}"


@pytest.mark.asyncio
async def test_start_command_is_routed_end_to_end():
    """A real update must travel dispatcher -> router -> handler -> reply."""
    dp = _build_dispatcher()

    bot = AsyncMock()
    bot.get_me = AsyncMock(
        return_value=SimpleNamespace(username="test_shop_bot", id=1, is_bot=True)
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

    # aiogram sends a method object to the bot, it does not call
    # ``bot.send_message`` — assert on the method that was built.
    bot.assert_awaited()
    method = bot.await_args.args[0]
    assert isinstance(method, SendMessage)
    assert method.chat_id == 555
    # the reply keyboard opens the Mini App
    markup = method.reply_markup
    assert markup.keyboard[0][0].web_app is not None
