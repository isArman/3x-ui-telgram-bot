"""Regression tests for the 3x-ui panel API contract.

The panel's own OpenAPI spec (``GET /panel/api/openapi.json``) is the source of
truth for these shapes; the tests below pin the parts the bot depends on.
"""

from datetime import datetime, timezone

import pytest

from app.services.traffic_usage import (
    client_record,
    parse_client_traffic,
    used_bytes,
)
from app.xui.client import (
    XUIClient,
    XUIError,
    prepare_client_update_payload,
    resolve_subscription_base_url,
)


# --- traffic shapes --------------------------------------------------------


def test_clients_get_envelope_uses_used_traffic():
    """`clients/get` returns `usedTraffic`, not a `traffic` object."""
    detail = {
        "client": {"email": "1", "totalGB": 10 * 1024**3, "expiryTime": 0},
        "inboundIds": [1],
        "usedTraffic": 4 * 1024**3,
        "externalLinks": [],
        "tunnelAllowedIPs": {},
    }

    assert used_bytes(detail) == 4 * 1024**3
    assert parse_client_traffic(detail) == (10 * 1024**3, 4 * 1024**3)


def test_clients_list_item_uses_traffic_object():
    """`clients/list` returns a flat client with a `traffic: {up, down}`."""
    item = {
        "email": "1",
        "totalGB": 10 * 1024**3,
        "traffic": {"up": 1024**3, "down": 2 * 1024**3},
    }

    assert used_bytes(item) == 3 * 1024**3
    assert parse_client_traffic(item) == (10 * 1024**3, 3 * 1024**3)
    assert client_record(item)["email"] == "1"


def test_unlimited_quota_has_no_traffic_figures():
    detail = {"client": {"totalGB": 0}, "usedTraffic": 5}
    assert parse_client_traffic(detail) is None


# --- client update payload -------------------------------------------------


def test_update_payload_carries_the_whole_record():
    """The panel replaces the row, so missing fields would wipe the client."""
    detail = {
        "client": {
            "id": 7,
            "uuid": "11111111-1111-1111-1111-111111111111",
            "email": "42",
            "auth": "secret-auth",
            "password": "secret-password",
            "security": "auto",
            "subId": "sub-1",
            "flow": "xtls-rprx-vision",
            "totalGB": 1024,
            "expiryTime": 100,
            "limitIp": 1,
            "tgId": 0,
            "reset": 0,
            "resetDay": 0,
            "resetMax": 0,
            "trafficReset": "never",
            "trafficResetDay": 1,
            "createdAt": 111,
            "updatedAt": 222,
        },
        "inboundIds": [1],
    }

    payload = prepare_client_update_payload(detail, email="42")

    # id is the protocol UUID, not the numeric row id
    assert payload["id"] == "11111111-1111-1111-1111-111111111111"
    # credentials and settings must survive the update
    assert payload["auth"] == "secret-auth"
    assert payload["password"] == "secret-password"
    assert payload["security"] == "auto"
    assert payload["subId"] == "sub-1"
    assert payload["flow"] == "xtls-rprx-vision"
    assert payload["trafficReset"] == "never"
    assert payload["created_at"] == 111
    assert payload["updated_at"] == 222


def test_update_payload_applies_the_requested_changes():
    detail = {"client": {"id": 7, "uuid": "u", "email": "42", "totalGB": 1}}
    payload = prepare_client_update_payload(
        detail, email="42", total_bytes=2048, expiry_ms=99, comment="hi"
    )
    assert payload["totalGB"] == 2048
    assert payload["expiryTime"] == 99
    assert payload["comment"] == "hi"


# --- subscription base url -------------------------------------------------


def test_subscription_base_uses_panel_sub_path_and_port():
    settings = {
        "subEnable": True,
        "subURI": "",
        "subPath": "/sub/",
        "subPort": 2096,
        "subDomain": "",
    }
    base = resolve_subscription_base_url(settings, "https://panel.example:2053/base/")
    assert base == "https://panel.example:2096/sub/"


def test_subscription_base_prefers_explicit_uri():
    settings = {"subEnable": True, "subURI": "https://sub.example.com/x/"}
    assert (
        resolve_subscription_base_url(settings, "https://panel.example:2053/")
        == "https://sub.example.com/x/"
    )


def test_subscription_base_requires_sub_enabled():
    with pytest.raises(XUIError):
        resolve_subscription_base_url({"subEnable": False}, "https://panel.example")


# --- authentication --------------------------------------------------------


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _HTTP:
    def __init__(self, get_payload=None, post_payload=None, post_status=200, post_statuses=None):
        self.get_calls = []
        self.post_calls = []
        self._get_payload = get_payload or {"success": True, "obj": []}
        self._post_payload = post_payload or {"success": True, "obj": {}}
        self._post_status = post_status
        self._post_statuses = list(post_statuses or [])

    async def get(self, url, headers=None):
        self.get_calls.append((url, headers))
        return _Resp(self._get_payload)

    async def post(self, url, json=None, headers=None):
        self.post_calls.append((url, json, headers))
        status = (
            self._post_statuses.pop(0)
            if self._post_statuses
            else self._post_status
        )
        return _Resp(self._post_payload, status)


@pytest.mark.asyncio
async def test_api_token_is_used_as_bearer_without_login_or_csrf():
    """Bearer callers skip the panel's CSRF check and the session login."""
    http = _HTTP(get_payload={"success": True, "obj": []})
    client = XUIClient("https://panel.example", api_token="tok-123")
    client._client = http

    await client.list_inbounds()

    assert http.get_calls[0][1] == {"Authorization": "Bearer tok-123"}
    # no login, no csrf fetch
    assert http.post_calls == []


@pytest.mark.asyncio
async def test_password_flow_still_sends_the_csrf_token_on_writes():
    http = _HTTP(
        get_payload={"success": True, "obj": "csrf-1"},
        post_payload={"success": True, "obj": {}},
    )
    client = XUIClient("https://panel.example", "admin", "secret")
    client._client = http

    await client._post_panel("/panel/api/clients/bulkAdjust", {"emails": ["1"]})

    assert http.post_calls[0][2]["X-CSRF-Token"] == "csrf-1"


@pytest.mark.asyncio
async def test_rejected_api_token_falls_back_to_the_password_login():
    """A revoked/expired token must not break the bot."""
    http = _HTTP(
        get_payload={"success": True, "obj": "csrf-1"},
        post_payload={"success": True, "obj": {}},
        post_statuses=[401],  # only the first write is rejected
    )
    client = XUIClient("https://panel.example", "admin", "secret", api_token="tok-123")
    client._client = http

    data = await client._post_panel("/panel/api/clients/bulkAdjust", {"emails": ["1"]})

    assert data["success"] is True
    # the token was dropped and the client fell back to a session login
    assert client.uses_api_token is False
    login_calls = [c for c in http.post_calls if c[1] and "username" in c[1]]
    assert login_calls and login_calls[0][1]["username"] == "admin"


@pytest.mark.asyncio
async def test_get_client_returns_none_for_a_missing_client():
    """The panel answers a missing client with success:false + a message."""
    http = _HTTP(get_payload={"success": False, "msg": "record not found"})
    client = XUIClient("https://panel.example", api_token="tok")
    client._client = http

    assert await client.get_client("nobody@example.com") is None


@pytest.mark.asyncio
async def test_get_client_raises_for_a_real_error():
    http = _HTTP(get_payload={"success": False, "msg": "database is locked"})
    client = XUIClient("https://panel.example", api_token="tok")
    client._client = http

    with pytest.raises(XUIError):
        await client.get_client("someone@example.com")
