"""Tests for CSRF caching / retry and panel write helper."""

import pytest

from app.xui.client import XUIClient, XUIError, _msg_is_stale_csrf


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
    def __init__(self, *, csrf_payloads=None, post_handler=None):
        self.csrf_calls = 0
        self.post_calls = []
        self._csrf_payloads = csrf_payloads or [{"success": True, "obj": "tok1"}]
        self._post_handler = post_handler

    async def get(self, url):
        payload = self._csrf_payloads[min(self.csrf_calls, len(self._csrf_payloads) - 1)]
        self.csrf_calls += 1
        return _Resp(payload)

    async def post(self, url, json=None, headers=None):
        self.post_calls.append({"url": url, "json": json, "headers": headers})
        return self._post_handler(len(self.post_calls), headers.get("X-CSRF-Token"))


def _client(http):
    c = XUIClient("https://panel.example.com", "admin", "secret")
    c._client = http
    return c


def test_msg_is_stale_csrf():
    assert _msg_is_stale_csrf("invalid csrf token") is True
    assert _msg_is_stale_csrf("token mismatch") is True
    assert _msg_is_stale_csrf("database is locked") is False


@pytest.mark.asyncio
async def test_csrf_token_is_cached_across_writes():
    """Two writes must fetch the CSRF token only once."""
    http = _HTTP(
        post_handler=lambda n, tok: _Resp({"success": True, "obj": {}}),
    )
    client = _client(http)

    await client._post_panel("/panel/api/clients/bulkAdjust", {"emails": ["1"]})
    await client._post_panel("/panel/api/clients/bulkAdjust", {"emails": ["2"]})

    assert http.csrf_calls == 1
    assert len(http.post_calls) == 2
    assert http.post_calls[0]["headers"]["X-CSRF-Token"] == "tok1"


@pytest.mark.asyncio
async def test_stale_csrf_retries_once_with_fresh_token():
    """A stale-token rejection refetches the token and retries the same call."""
    http = _HTTP(
        csrf_payloads=[
            {"success": True, "obj": "old"},
            {"success": True, "obj": "new"},
        ],
        post_handler=lambda n, tok: (
            _Resp({"success": False, "msg": "invalid csrf token"})
            if n == 1
            else _Resp({"success": True, "obj": {"adjusted": 1}})
        ),
    )
    client = _client(http)

    data = await client._post_panel(
        "/panel/api/clients/bulkAdjust", {"emails": ["1"]}
    )

    assert data["obj"] == {"adjusted": 1}
    assert http.csrf_calls == 2
    assert len(http.post_calls) == 2
    assert http.post_calls[0]["headers"]["X-CSRF-Token"] == "old"
    assert http.post_calls[1]["headers"]["X-CSRF-Token"] == "new"


@pytest.mark.asyncio
async def test_real_error_is_raised_without_retry():
    http = _HTTP(
        post_handler=lambda n, tok: _Resp({"success": False, "msg": "boom"}),
    )
    client = _client(http)

    with pytest.raises(XUIError):
        await client._post_panel("/panel/api/clients/bulkAdjust", {"emails": ["1"]})

    assert len(http.post_calls) == 1
