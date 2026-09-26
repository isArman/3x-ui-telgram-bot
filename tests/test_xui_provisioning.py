"""Tests for 3x-ui client provisioning: atomic extend + re-enable (A/B fixes)."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import pytest

from app.database.models import PanelSettings
from app.services.panel_settings import set_panel_password, set_selected_inbound_ids
from app.services.xui_provisioning import (
    _bulk_adjust_skipped,
    provision_subscription_for_order,
)
from app.xui.client import XUIClient, XUIError, _msg_is_not_found


# --- helpers ---------------------------------------------------------------


def _panel_settings() -> PanelSettings:
    ps = PanelSettings(id=1, provisioning_mode="auto", is_verified=True)
    ps.panel_url = "https://panel.example.com"
    ps.panel_username = "admin"
    set_panel_password(ps, "secret")
    ps.subscription_base_url = "https://panel.example.com/sub/"
    set_selected_inbound_ids(ps, [1])
    return ps


class _FakeHTTPResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakePanel:
    """Minimal XUIClient stand-in that records the calls made to it."""

    def __init__(self, *, existing_client=None, bulk_adjust_result=None):
        self.existing_client = existing_client
        self.bulk_adjust_result = bulk_adjust_result or {"adjusted": 1, "skipped": []}
        self.bulk_adjust_calls = []
        self.add_client_calls = []
        self.sync_calls = []

    async def list_inbounds(self):
        return [{"id": 1, "enable": True}]

    async def get_client(self, email):
        return self.existing_client

    async def bulk_adjust(self, emails, *, add_days=0, add_bytes=0):
        self.bulk_adjust_calls.append(
            {"emails": emails, "add_days": add_days, "add_bytes": add_bytes}
        )
        return self.bulk_adjust_result

    async def sync_client_inbounds(self, email, inbound_ids):
        self.sync_calls.append((email, inbound_ids))
        return self.existing_client

    async def add_client(self, email, inbound_ids, total_bytes, expiry_ms, comment, **kw):
        self.add_client_calls.append(
            {
                "email": email,
                "inbound_ids": inbound_ids,
                "total_bytes": total_bytes,
                "expiry_ms": expiry_ms,
                "comment": comment,
            }
        )
        return {"client": {"subId": "sub-new", "email": email, "enable": True}}


class _User:
    id = 5821190149
    username = "tester"
    first_name = "Test"
    last_name = "User"


class _Order:
    id = 7
    user_id = 5821190149
    days = 30
    traffic_gb = 20


def _patch_panel(monkeypatch, fake_panel):
    @asynccontextmanager
    async def _ctx(_settings):
        yield fake_panel

    import app.services.xui_provisioning as mod

    monkeypatch.setattr(mod, "xui_client_for_panel", _ctx)


# --- provisioning behavior -------------------------------------------------


@pytest.mark.asyncio
async def test_existing_client_is_extended_atomically(monkeypatch):
    """A repeat purchase/renewal extends via bulkAdjust (delta), not a reset."""
    existing = {"client": {"email": str(_User.id), "enable": False, "subId": "sub-1"}}
    fake = _FakePanel(existing_client=existing)
    _patch_panel(monkeypatch, fake)

    url = await provision_subscription_for_order(
        None, _panel_settings(), _User(), _Order()
    )

    assert len(fake.bulk_adjust_calls) == 1
    call = fake.bulk_adjust_calls[0]
    assert call["emails"] == [str(_User.id)]
    assert call["add_days"] == 30
    assert call["add_bytes"] == 20 * 1024**3
    # No create happened, and membership was re-synced.
    assert fake.add_client_calls == []
    assert fake.sync_calls == [(str(_User.id), [1])]
    assert url == "https://panel.example.com/sub/sub-1"


@pytest.mark.asyncio
async def test_brand_new_client_is_created(monkeypatch):
    """A user with no panel client yet is created with the order's quota."""
    fake = _FakePanel(existing_client=None)
    _patch_panel(monkeypatch, fake)

    url = await provision_subscription_for_order(
        None, _panel_settings(), _User(), _Order()
    )

    assert fake.bulk_adjust_calls == []
    assert len(fake.add_client_calls) == 1
    created = fake.add_client_calls[0]
    assert created["email"] == str(_User.id)
    assert created["total_bytes"] == 20 * 1024**3
    assert created["inbound_ids"] == [1]
    assert url == "https://panel.example.com/sub/sub-new"


@pytest.mark.asyncio
async def test_skipped_adjust_does_not_report_success(monkeypatch):
    """If the panel skips the email, provisioning must fail (fallback path)."""
    existing = {"client": {"email": str(_User.id), "enable": True, "subId": "s"}}
    fake = _FakePanel(
        existing_client=existing,
        bulk_adjust_result={
            "adjusted": 0,
            "skipped": [{"email": str(_User.id), "reason": "client not found"}],
        },
    )
    _patch_panel(monkeypatch, fake)

    url = await provision_subscription_for_order(
        None, _panel_settings(), _User(), _Order()
    )

    assert url is None


# --- bulkAdjust client call ------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_adjust_sends_delta_body(monkeypatch):
    client = XUIClient("https://panel.example.com", "admin", "secret")
    captured = {}

    class _HTTP:
        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            return _FakeHTTPResponse({"success": True, "obj": {"adjusted": 1}})

    async def _fake_csrf(*, force: bool = False):
        return "tok"

    monkeypatch.setattr(client, "_csrf_token", _fake_csrf)
    client._client = _HTTP()

    result = await client.bulk_adjust(["123"], add_days=30, add_bytes=1024)

    assert captured["url"].endswith("/panel/api/clients/bulkAdjust")
    assert captured["json"] == {
        "emails": ["123"],
        "addDays": 30,
        "addBytes": 1024,
    }
    assert result == {"adjusted": 1}


@pytest.mark.asyncio
async def test_bulk_adjust_raises_on_panel_error(monkeypatch):
    client = XUIClient("https://panel.example.com", "admin", "secret")

    class _HTTP:
        async def post(self, url, json=None, headers=None):
            return _FakeHTTPResponse({"success": False, "msg": "boom"})

    async def _fake_csrf(*, force: bool = False):
        return "tok"

    monkeypatch.setattr(client, "_csrf_token", _fake_csrf)
    client._client = _HTTP()

    with pytest.raises(XUIError):
        await client.bulk_adjust(["123"], add_days=1)


@pytest.mark.asyncio
async def test_bulk_adjust_requires_a_change():
    client = XUIClient("https://panel.example.com", "admin", "secret")
    with pytest.raises(ValueError):
        await client.bulk_adjust(["123"])


# --- helpers ---------------------------------------------------------------


def test_bulk_adjust_skipped_detects_email():
    result = {"adjusted": 0, "skipped": [{"email": "a", "reason": "x"}]}
    assert _bulk_adjust_skipped(result, "a") is True
    assert _bulk_adjust_skipped(result, "b") is False
    assert _bulk_adjust_skipped({}, "a") is False


def test_msg_is_not_found():
    assert _msg_is_not_found("record not found") is True
    assert _msg_is_not_found("Client Not Found In Inbound") is True
    assert _msg_is_not_found("database is locked") is False
    assert _msg_is_not_found("") is False


# --- get_client error semantics -------------------------------------------


@pytest.mark.asyncio
async def test_get_client_returns_none_when_missing(monkeypatch):
    client = XUIClient("https://panel.example.com", "admin", "secret")

    class _HTTP:
        async def get(self, url):
            return _FakeHTTPResponse({"success": False, "msg": "record not found"})

    client._client = _HTTP()
    assert await client.get_client("123") is None


@pytest.mark.asyncio
async def test_get_client_raises_on_real_error(monkeypatch):
    """A transient panel error must not be mistaken for 'client missing'."""
    client = XUIClient("https://panel.example.com", "admin", "secret")

    class _HTTP:
        async def get(self, url):
            return _FakeHTTPResponse({"success": False, "msg": "database is locked"})

    client._client = _HTTP()
    with pytest.raises(XUIError):
        await client.get_client("123")
