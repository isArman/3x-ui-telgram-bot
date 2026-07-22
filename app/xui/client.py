from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from app.config.settings import settings
from app.utils.logger import logger


class XUIError(Exception):
    pass


def prepare_client_update_payload(
    existing_detail: dict[str, Any],
    *,
    email: str,
    total_bytes: int,
    expiry_ms: int,
    comment: str,
) -> dict[str, Any]:
    """
    Build the JSON body for POST /panel/api/clients/update/:email.

    GET returns a numeric DB row id in client.id, but UPDATE expects the protocol
    UUID as a string — sending the number triggers a Go unmarshal error.
    """
    old = dict(existing_detail.get("client") or {})
    payload: dict[str, Any] = {
        "email": email,
        "enable": True,
        "totalGB": total_bytes,
        "expiryTime": expiry_ms,
        "comment": comment,
        "limitIp": old.get("limitIp", 0),
        "tgId": old.get("tgId", 0),
    }

    for key in ("subId", "flow", "password", "auth"):
        value = old.get(key)
        if value not in (None, ""):
            payload[key] = value

    protocol_id = old.get("id")
    if isinstance(protocol_id, str) and protocol_id:
        payload["id"] = protocol_id
    else:
        uuid_val = existing_detail.get("uuid") or old.get("uuid")
        if uuid_val:
            payload["id"] = str(uuid_val)

    return payload


class XUIClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        verify_ssl: bool | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.verify_ssl = settings.XUI_VERIFY_SSL if verify_ssl is None else verify_ssl
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "XUIClient":
        self._client = httpx.AsyncClient(
            verify=self.verify_ssl,
            timeout=30.0,
            follow_redirects=True,
        )
        await self.login()
        return self

    async def __aexit__(self, *args) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{self.base_url}{path}"

    async def _csrf_token(self) -> str:
        assert self._client
        resp = await self._client.get(self._url("/csrf-token"))
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise XUIError(data.get("msg") or "Failed to get CSRF token")
        return data["obj"]

    async def login(self) -> None:
        assert self._client
        await self._client.get(self._url("/"))
        token = await self._csrf_token()
        resp = await self._client.post(
            self._url("/login"),
            json={"username": self.username, "password": self.password},
            headers={"X-CSRF-Token": token},
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise XUIError(data.get("msg") or "Login failed")

    async def test_connection(self) -> dict[str, Any]:
        """Login and return a short summary (inbound count + subscription base)."""
        inbounds = await self.list_inbounds()
        enabled = [ib for ib in inbounds if ib.get("enable")]
        panel_settings = await self.fetch_settings()
        sub_base = resolve_subscription_base_url(panel_settings, self.base_url)
        return {
            "total_inbounds": len(inbounds),
            "enabled_inbounds": len(enabled),
            "inbounds": inbounds,
            "subscription_base_url": sub_base,
            "sub_enable": panel_settings.get("subEnable", False),
        }

    async def fetch_settings(self) -> dict[str, Any]:
        assert self._client
        token = await self._csrf_token()
        resp = await self._client.post(
            self._url("/panel/api/setting/all"),
            headers={"X-CSRF-Token": token},
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise XUIError(data.get("msg") or "Failed to fetch panel settings")
        return data.get("obj") or {}

    async def get_subscription_base_url(self) -> str:
        settings = await self.fetch_settings()
        return resolve_subscription_base_url(settings, self.base_url)

    async def list_inbounds(self) -> list[dict[str, Any]]:
        assert self._client
        resp = await self._client.get(self._url("/panel/api/inbounds/list"))
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise XUIError(data.get("msg") or "Failed to list inbounds")
        return data.get("obj") or []

    async def get_client(self, email: str) -> Optional[dict[str, Any]]:
        assert self._client
        resp = await self._client.get(
            self._url(f"/panel/api/clients/get/{email}")
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            return None
        return data.get("obj")

    async def add_client(
        self,
        email: str,
        inbound_ids: list[int],
        total_bytes: int,
        expiry_ms: int,
        comment: str,
    ) -> dict[str, Any]:
        assert self._client
        token = await self._csrf_token()
        payload = {
            "client": {
                "email": email,
                "enable": True,
                "totalGB": total_bytes,
                "expiryTime": expiry_ms,
                "limitIp": 0,
                "tgId": 0,
                "comment": comment,
            },
            "inboundIds": inbound_ids,
        }
        resp = await self._client.post(
            self._url("/panel/api/clients/add"),
            json=payload,
            headers={"X-CSRF-Token": token},
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise XUIError(data.get("msg") or "Failed to add client")
        detail = await self.get_client(email)
        if not detail:
            raise XUIError("Client created but could not be fetched")
        return detail

    async def update_client(
        self,
        email: str,
        client_payload: dict[str, Any],
        inbound_ids: list[int],
    ) -> dict[str, Any]:
        assert self._client
        token = await self._csrf_token()
        resp = await self._client.post(
            self._url(f"/panel/api/clients/update/{email}"),
            json=client_payload,
            headers={"X-CSRF-Token": token},
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise XUIError(data.get("msg") or "Failed to update client")

        detail = await self.get_client(email)
        if detail:
            current_ids = set(detail.get("inboundIds") or [])
            missing = [i for i in inbound_ids if i not in current_ids]
            if missing:
                attach_token = await self._csrf_token()
                attach_resp = await self._client.post(
                    self._url(f"/panel/api/clients/{email}/attach"),
                    json={"inboundIds": missing},
                    headers={"X-CSRF-Token": attach_token},
                )
                attach_resp.raise_for_status()
                attach_data = attach_resp.json()
                if not attach_data.get("success"):
                    logger.warning(
                        "Failed to attach client %s to inbounds %s: %s",
                        email,
                        missing,
                        attach_data.get("msg"),
                    )
            detail = await self.get_client(email)
        if not detail:
            raise XUIError("Client updated but could not be fetched")
        return detail

    async def upsert_client(
        self,
        email: str,
        inbound_ids: list[int],
        total_bytes: int,
        expiry_ms: int,
        comment: str,
    ) -> dict[str, Any]:
        existing = await self.get_client(email)
        if existing and existing.get("client"):
            client = prepare_client_update_payload(
                existing,
                email=email,
                total_bytes=total_bytes,
                expiry_ms=expiry_ms,
                comment=comment,
            )
            return await self.update_client(email, client, inbound_ids)
        return await self.add_client(
            email, inbound_ids, total_bytes, expiry_ms, comment
        )


def normalize_panel_url(raw: str) -> str:
    raw = raw.strip().rstrip("/")
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("URL must include scheme and host, e.g. https://host:2053/path")
    return raw


def resolve_subscription_base_url(
    panel_settings: dict[str, Any],
    panel_url: str,
) -> str:
    """
    Build subscription base URL from 3x-ui panel settings (Settings → Subscription).
    Returns URL ending with / — append subId for the full subscription link.
    """
    if not panel_settings.get("subEnable", False):
        raise XUIError(
            "Subscription در تنظیمات پنل غیرفعال است. "
            "از Settings → Subscription آن را فعال کنید."
        )

    sub_uri = (panel_settings.get("subURI") or "").strip()
    if sub_uri:
        return sub_uri if sub_uri.endswith("/") else f"{sub_uri}/"

    parsed = urlparse(panel_url)
    scheme = parsed.scheme or "https"

    sub_domain = (
        (panel_settings.get("subDomain") or panel_settings.get("webDomain") or "")
        .strip()
    )
    host = sub_domain or parsed.hostname
    if not host:
        raise XUIError("Could not determine subscription host from panel settings")

    sub_port = int(panel_settings.get("subPort") or 0)
    sub_path = panel_settings.get("subPath") or "/sub/"
    if not sub_path.startswith("/"):
        sub_path = f"/{sub_path}"
    if not sub_path.endswith("/"):
        sub_path = f"{sub_path}/"

    if sub_port <= 0:
        sub_port = parsed.port or (443 if scheme == "https" else 80)

    default_port = 443 if scheme == "https" else 80
    netloc = host if sub_port == default_port else f"{host}:{sub_port}"

    return f"{scheme}://{netloc}{sub_path}"


def build_subscription_url(base_url: str, sub_id: str) -> str:
    base = base_url.rstrip("/") + "/"
    return f"{base}{sub_id}"
