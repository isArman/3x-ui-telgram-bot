"""Tests for the Mini App: launch-data authentication and the JSON API."""

import hashlib
import hmac
import json
import pathlib
import re
import time
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from app.database.models import User
from app.database.session import AsyncSessionLocal, init_db
from app.web.auth import WebAuthError, parse_user, validate_init_data
from app.web.server import create_app

BOT_TOKEN = "123456:TEST-TOKEN"


def make_init_data(user_id: int = 555, *, token: str = BOT_TOKEN, age: int = 0) -> str:
    """Build valid launch data the way Telegram does."""
    fields = {
        "auth_date": str(int(time.time()) - age),
        "query_id": "AAH_test",
        "user": json.dumps(
            {
                "id": user_id,
                "first_name": "Test",
                "last_name": "User",
                "username": "tester",
            }
        ),
    }
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(
        secret, check_string.encode(), hashlib.sha256
    ).hexdigest()
    return urlencode(fields)


# --- authentication --------------------------------------------------------


def test_valid_init_data_is_accepted():
    data = validate_init_data(make_init_data(), BOT_TOKEN)
    assert data["query_id"] == "AAH_test"

    user = parse_user(data)
    assert user is not None
    assert user["id"] == 555
    assert user["username"] == "tester"


def test_tampered_init_data_is_rejected():
    data = make_init_data()
    tampered = data.replace("tester", "hacker")
    with pytest.raises(WebAuthError):
        validate_init_data(tampered, BOT_TOKEN)


def test_init_data_signed_with_another_token_is_rejected():
    data = make_init_data(token="999:OTHER")
    with pytest.raises(WebAuthError):
        validate_init_data(data, BOT_TOKEN)


def test_expired_init_data_is_rejected():
    data = make_init_data(age=48 * 3600)
    with pytest.raises(WebAuthError):
        validate_init_data(data, BOT_TOKEN)


def test_missing_init_data_is_rejected():
    with pytest.raises(WebAuthError):
        validate_init_data("", BOT_TOKEN)


# --- API ------------------------------------------------------------------


@pytest.fixture()
async def client(monkeypatch):
    # BOT_TOKEN is read from the environment on every access.
    monkeypatch.setenv("BOT_TOKEN", BOT_TOKEN)
    await init_db()

    bot = type("Bot", (), {"get_me": _fake_get_me})()
    app = create_app(bot)
    server = TestServer(app)
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        yield test_client
    finally:
        await test_client.close()


async def _fake_get_me(self):
    class _Me:
        username = "test_shop_bot"
        id = 1
        is_bot = True

    return _Me()


async def _auth(client, user_id: int = 555):
    return {
        "X-Telegram-Init-Data": make_init_data(user_id),
    }


async def test_api_requires_valid_launch_data(client):
    resp = await client.get("/api/me")
    assert resp.status == 401


async def test_api_rejects_tampered_launch_data(client):
    resp = await client.get(
        "/api/me",
        headers={"X-Telegram-Init-Data": make_init_data().replace("tester", "x")},
    )
    assert resp.status == 401


async def test_me_returns_the_profile(client):
    resp = await client.get("/api/me", headers=await _auth(client))
    assert resp.status == 200
    body = await resp.json()
    assert body["ok"] is True
    data = body["data"]
    assert data["user"]["id"] == 555
    assert "balance" in data
    assert "referral_code" in data


async def test_plans_endpoint_lists_active_plans(client):
    from app.database.models import ShopPlan

    async with AsyncSessionLocal() as session:
        session.add(
            ShopPlan(id="apiplan", name="Basic", days=30, traffic_gb=50, price=100000)
        )
        session.add(
            ShopPlan(
                id="apioff", name="Off", days=30, traffic_gb=50, price=1, is_active=False
            )
        )
        await session.commit()

    resp = await client.get("/api/plans", headers=await _auth(client))
    body = await resp.json()
    ids = [p["id"] for p in body["data"]["plans"]]
    assert "apiplan" in ids
    assert "apioff" not in ids


async def test_quote_applies_a_promo_code(client):
    from app.database.models import ShopPlan
    from app.services.promocodes import create_promo

    async with AsyncSessionLocal() as session:
        session.add(
            ShopPlan(id="quoteplan", name="Basic", days=30, traffic_gb=50, price=100000)
        )
        await session.commit()
        await create_promo(session, code="HALF", kind="percent", value=50)
        await session.commit()

    resp = await client.post(
        "/api/quote",
        headers=await _auth(client),
        json={"plan_id": "quoteplan", "promo_code": "HALF"},
    )
    body = await resp.json()
    assert body["ok"] is True
    data = body["data"]
    assert data["base_price"] == 100000
    assert data["promo_discount"] == 50000
    assert data["total"] == 50000


async def test_admin_routes_are_forbidden_for_customers(client):
    resp = await client.get("/api/admin/dashboard", headers=await _auth(client))
    assert resp.status == 403
    body = await resp.json()
    assert body["ok"] is False
    assert body["code"] == "forbidden"


async def test_index_page_is_served(client):
    resp = await client.get("/")
    assert resp.status == 200
    body = await resp.text()
    assert "telegram-web-app.js" in body


async def test_static_assets_are_served(client):
    for path in ("/assets/app.js", "/assets/app.css"):
        resp = await client.get(path)
        assert resp.status == 200, path
        assert await resp.text()


# --- the frontend must not call an endpoint that does not exist ------------


def _api_calls(source: str) -> list[str]:
    """The argument list of every ``api(...)`` call in the Mini App."""
    calls = []
    for match in re.finditer(r"\bapi\(", source):
        index = match.end()
        depth = 1
        while index < len(source) and depth:
            char = source[index]
            if char in "([{":
                depth += 1
            elif char in ")]}":
                depth -= 1
            index += 1
        calls.append(source[match.end() : index - 1])
    return calls


def _split_args(args: str) -> list[str]:
    parts = []
    depth = 0
    current = ""
    for char in args:
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
            continue
        current += char
    if current.strip():
        parts.append(current)
    return parts


def _frontend_calls() -> list[tuple[str, str]]:
    """(method, path) of every API call the Mini App makes."""
    source = pathlib.Path("app/web/static/app.js").read_text()
    calls = []
    for args in _api_calls(source):
        parts = _split_args(args)
        if not parts:
            continue
        expression = parts[0]
        pieces = []
        last = 0
        for literal in re.finditer(r'"([^"]*)"', expression):
            if expression[last : literal.start()].strip():
                pieces.append("{x}")
            pieces.append(literal.group(1))
            last = literal.end()
        if expression[last:].strip():
            pieces.append("{x}")
        path = "".join(pieces)
        if not path.startswith("/api/"):
            continue
        path = path.split("?")[0]
        options = " ".join(parts[1:])
        method = "POST" if "body" in options else "GET"
        explicit = re.search(r'method:\s*"(\w+)"', options)
        if explicit:
            method = explicit.group(1)
        calls.append((method, path))
    return calls


def _server_routes() -> set[tuple[str, str]]:
    from app.web import admin_api, api

    routes = set()
    for table in (api.routes, admin_api.routes):
        for route in table:
            path = re.sub(r"\{[^}]+\}", "{x}", route.path)
            routes.add((route.method, path))
    return routes


def test_frontend_only_calls_registered_endpoints():
    """The Mini App must not call an endpoint the server does not serve."""
    known = _server_routes()
    missing = []
    for method, path in _frontend_calls():
        if (method, path) in known:
            continue
        # A dynamic tail (e.g. "/api/orders/{x}") only needs a matching prefix.
        if path.endswith("/{x}"):
            prefix = path[: -len("{x}")]
            if any(route[0] == method and route[1].startswith(prefix) for route in known):
                continue
        missing.append(f"{method} {path}")
    assert not missing, f"The Mini App calls unknown endpoints: {missing}"


def test_frontend_uses_the_response_envelope():
    """The Mini App reads ``data`` from the standard envelope."""
    source = pathlib.Path("app/web/static/app.js").read_text()
    assert "payload.data" in source
    assert "payload.ok === false" in source
