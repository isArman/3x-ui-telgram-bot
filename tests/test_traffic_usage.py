from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.bot.handlers import user as user_handlers
from app.bot.handlers.user import format_panel_account_text
from app.bot.keyboards.user import accounts_list_keyboard
from app.services.traffic_usage import (
    GB,
    is_low_traffic,
    parse_client_traffic,
    remaining_traffic_percent,
)


def test_parse_client_traffic():
    detail = {
        "client": {"totalGB": 10 * GB},
        "traffic": {"up": 2 * GB, "down": 3 * GB},
    }
    assert parse_client_traffic(detail) == (10 * GB, 5 * GB)


def test_parse_client_traffic_unlimited():
    detail = {"client": {"totalGB": 0}, "traffic": {"up": 999, "down": 999}}
    assert parse_client_traffic(detail) is None


def test_remaining_percent():
    total = 10 * GB
    used = 9 * GB
    assert remaining_traffic_percent(total, used) == 10.0
    assert is_low_traffic(total, used) is False

    used_low = int(9.1 * GB)
    assert remaining_traffic_percent(total, used_low) < 10.0
    assert is_low_traffic(total, used_low) is True


def test_panel_account_text_uses_current_api_values():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    detail = {
        "client": {
            "email": "700700",
            "totalGB": 10 * GB,
            "expiryTime": int((now + timedelta(days=30)).timestamp() * 1000),
            "enable": True,
            "limitIp": 1,
            "subId": "abc123",
        },
        "traffic": {"up": 2 * GB, "down": 3 * GB},
    }

    text = format_panel_account_text(detail, "https://sub.example/", now=now)

    assert "حجم کل: 10.00 گیگابایت" in text
    assert "مصرف‌شده: 5.00 گیگابایت" in text
    assert "باقی‌مانده: 5.00 گیگابایت" in text
    assert "روزهای باقی‌مانده: 30" in text
    assert "وضعیت: فعال" in text
    assert "https://sub.example/abc123" in text


def test_panel_account_text_handles_unlimited_and_expired_client():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    detail = {
        "client": {
            "email": "700701",
            "totalGB": 0,
            "expiryTime": int((now - timedelta(days=1)).timestamp() * 1000),
            "enable": True,
            "limitIp": 0,
        },
        "traffic": {"up": GB, "down": 0},
    }

    text = format_panel_account_text(detail, None, now=now)

    assert "حجم: نامحدود" in text
    assert "مصرف‌شده: 1.00 گیگابایت" in text
    assert "وضعیت: منقضی شده" in text
    assert "لینک اشتراک:\nدر دسترس نیست" in text


def test_accounts_list_keyboard_renews_panel_client_email():
    keyboard = accounts_list_keyboard("700700")

    assert keyboard.inline_keyboard[0][0].callback_data == "renew_client:700700"
    assert keyboard.inline_keyboard[0][0].text == "تمدید اکانت"


@pytest.mark.asyncio
async def test_send_accounts_list_fetches_account_from_panel_api():
    class Session:
        execute = AsyncMock()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    client = SimpleNamespace(
        get_client=AsyncMock(
            return_value={
                "client": {"email": "700702", "totalGB": GB},
                "traffic": {"up": 0, "down": 0},
            }
        )
    )
    panel = SimpleNamespace(
        panel_url="https://panel.example",
        panel_username="admin",
        subscription_base_url="https://sub.example/",
    )
    bot = SimpleNamespace(send_message=AsyncMock())
    event = SimpleNamespace(
        from_user=SimpleNamespace(id=700702),
        bot=bot,
        message=SimpleNamespace(chat=SimpleNamespace(id=700702)),
    )

    @asynccontextmanager
    async def panel_client_context(_panel):
        yield client

    with (
        patch.object(user_handlers, "AsyncSessionLocal", return_value=Session()),
        patch.object(user_handlers, "get_panel_settings", new=AsyncMock(return_value=panel)),
        patch.object(user_handlers, "get_panel_password", return_value="secret"),
        patch.object(user_handlers, "xui_client_for_panel", panel_client_context),
    ):
        await user_handlers.send_accounts_list(event)

    client.get_client.assert_awaited_once_with("700702")
    bot.send_message.assert_awaited_once()
    assert "اطلاعات اکانت شما از پنل 3x-ui" in bot.send_message.await_args.kwargs["text"]
    Session.execute.assert_not_awaited()
