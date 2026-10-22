"""
Client for the 3x-ui panel API.

Endpoints used (see the panel's own OpenAPI spec at
``GET /panel/api/openapi.json``):

===========================================  ======  ==========================
Endpoint                                     Method  Purpose
===========================================  ======  ==========================
``/panel/api/clients/get/:email``           GET     one client + inbound ids
``/panel/api/clients/traffic/:email``       GET     up/down traffic counters
``/panel/api/clients/list``                 GET     every client + traffic
``/panel/api/clients/add``                  POST    create a client
``/panel/api/clients/update/:email``        POST    replace a client record
``/panel/api/clients/:email/attach``        POST    attach to inbounds
``/panel/api/clients/:email/detach``        POST    detach from inbounds
``/panel/api/clients/bulkAdjust``           POST    add days / quota to clients
``/panel/api/inbounds/list``                GET     configured inbounds
``/panel/api/setting/all``                  POST    panel settings (subscription)
``/panel/api/setting/apiTokens``            GET     list API tokens
``/panel/api/setting/apiTokens/create``     POST    mint an API token
===========================================  ======  ==========================

Two authentication modes are supported:

* **API token** (preferred) — ``Authorization: Bearer <token>``.  The panel
  treats bearer callers as non-browser sessions and skips the CSRF check
  entirely, so there is no session cookie, no CSRF token and no login.  It
  also side-steps the panel's login limiter, which blocks an
  (ip, username) pair for 15 minutes after 5 failed logins in 5 minutes.
* **Username + password** (fallback) — the legacy session flow: log in, keep
  the session cookie and echo the session's CSRF token on unsafe methods.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from app.config.settings import settings
from app.utils.logger import logger


class XUIError(Exception):
    pass


# Concurrent device/IP limit enforced for every subscription (existing + new).
DEFAULT_CLIENT_LIMIT_IP = 1

# Admin-scoped API token: full access to the panel API, like the password.
API_TOKEN_SCOPE_ADMIN = "admin"

_NOT_FOUND_HINTS = (
    "record not found",
    "not found",
    "no such",
    "does not exist",
)

_CSRF_HINTS = ("csrf", "invalid token", "token mismatch", "expired")


def _msg_is_not_found(msg: str) -> bool:
    """True when a panel message means "this client does not exist"."""
    lowered = (msg or "").lower()
    return any(hint in lowered for hint in _NOT_FOUND_HINTS)


def _msg_is_stale_csrf(msg: str) -> bool:
    """True when a panel message points at a rejected/expired CSRF token."""
    lowered = (msg or "").lower()
    return "csrf" in lowered or any(hint in lowered for hint in _CSRF_HINTS[1:])


# The panel's inbound-settings client shape. `POST /clients/update/:email`
# replaces the stored row with exactly these fields, so a partial body wipes
# the credentials of the client. Everything not listed here is dropped by the
# server.
CLIENT_RECORD_FIELDS = (
    "adTag",
    "allowedIPs",
    "auth",
    "comment",
    "email",
    "enable",
    "expiryTime",
    "flow",
    "forwardedPorts",
    "group",
    "keepAlive",
    "limitIp",
    "password",
    "preSharedKey",
    "privateKey",
    "publicKey",
    "reset",
    "resetDay",
    "resetMax",
    "resetWeekday",
    "reverse",
    "secret",
    "security",
    "subId",
    "tgId",
    "totalGB",
    "trafficReset",
    "trafficResetDay",
)


def prepare_client_update_payload(
    existing_detail: dict[str, Any],
    *,
    email: str,
    total_bytes: int | None = None,
    expiry_ms: int | None = None,
    comment: str | None = None,
    limit_ip: int = DEFAULT_CLIENT_LIMIT_IP,
) -> dict[str, Any]:
    """
    Build the JSON body for ``POST /panel/api/clients/update/:email``.

    The server *replaces* the client row with the body, so the payload has to
    carry the whole record — sending only the changed fields would blank out
    the client's auth/password and break the subscription.

    The stored record uses ``id`` (numeric row id) and ``uuid`` (the protocol
    UUID); the update endpoint expects the inbound-settings shape, where
    ``id`` is that UUID string and the timestamps are named ``created_at`` /
    ``updated_at``.
    """
    old = dict(existing_detail.get("client") or {})

    payload: dict[str, Any] = {}
    for field in CLIENT_RECORD_FIELDS:
        if field in old:
            payload[field] = old[field]

    # id is the protocol UUID for the update endpoint.
    uuid_value = old.get("uuid") or existing_detail.get("uuid")
    if isinstance(uuid_value, str) and uuid_value:
        payload["id"] = uuid_value
    elif isinstance(old.get("id"), str) and old["id"]:
        payload["id"] = old["id"]

    if old.get("createdAt") is not None:
        payload["created_at"] = old.get("createdAt")
    if old.get("updatedAt") is not None:
        payload["updated_at"] = old.get("updatedAt")

    payload["email"] = email
    if total_bytes is not None:
        payload["totalGB"] = int(total_bytes)
    if expiry_ms is not None:
        payload["expiryTime"] = int(expiry_ms)
    if comment is not None:
        payload["comment"] = comment
    if limit_ip is not None:
        payload["limitIp"] = int(limit_ip)

    return payload


@dataclass
class ClientTraffic:
    """Traffic counters for one client."""

    up: int = 0
    down: int = 0
    total: int = 0

    @property
    def used(self) -> int:
        return self.up + self.down


class XUIClient:
    def __init__(
        self,
        base_url: str,
        username: str = "",
        password: str = "",
        *,
        api_token: str | None = None,
        verify_ssl: bool | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.api_token = (api_token or "").strip()
        self.verify_ssl = settings.XUI_VERIFY_SSL if verify_ssl is None else verify_ssl
        self._client: Optional[httpx.AsyncClient] = None
        self._csrf_token_cache: str = ""
        self._logged_in = False

    # --- lifecycle ---------------------------------------------------------

    @property
    def uses_api_token(self) -> bool:
        return bool(self.api_token)

    async def __aenter__(self) -> "XUIClient":
        self._client = httpx.AsyncClient(
            verify=self.verify_ssl,
            timeout=30.0,
            follow_redirects=True,
        )
        if not self.uses_api_token:
            await self.login()
        return self

    async def __aexit__(self, *args) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        self._csrf_token_cache = ""
        self._logged_in = False

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{self.base_url}{path}"

    # --- authentication ----------------------------------------------------

    async def _csrf_token(self, *, force: bool = False) -> str:
        """
        Return the session's CSRF token, caching it in memory.

        Only the username/password flow needs this: bearer-token callers skip
        the CSRF check on the panel side.
        """
        assert self._client
        if self._csrf_token_cache and not force:
            return self._csrf_token_cache
        resp = await self._client.get(self._url("/csrf-token"))
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise XUIError(data.get("msg") or "Failed to get CSRF token")
        self._csrf_token_cache = data["obj"]
        return self._csrf_token_cache

    def _invalidate_csrf(self) -> None:
        self._csrf_token_cache = ""

    async def _headers(self, *, unsafe: bool) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.uses_api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
            return headers
        if unsafe:
            headers["X-CSRF-Token"] = await self._csrf_token()
        return headers

    async def login(self) -> None:
        """Log in with username/password and prime the session CSRF token."""
        assert self._client
        self._invalidate_csrf()
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
        # The authenticated session may issue its own CSRF token; drop the one
        # minted for the anonymous session so the next write mints a fresh one.
        self._invalidate_csrf()
        self._logged_in = True

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        error_label: str = "request",
    ) -> dict[str, Any]:
        """
        Perform an authenticated API call and return the JSON envelope.

        With a bearer token there is nothing to refresh, so a failure is final.
        With the session flow a 401 means the session died and a 403 usually
        means the CSRF token went stale; both are retried once.
        """
        assert self._client
        url = self._url(path)
        unsafe = method.upper() not in ("GET", "HEAD", "OPTIONS", "TRACE")

        for attempt in range(2):
            headers = await self._headers(unsafe=unsafe)
            if method.upper() == "GET":
                if headers:
                    resp = await self._client.get(url, headers=headers)
                else:
                    resp = await self._client.get(url)
            else:
                resp = await self._client.post(url, json=json, headers=headers)

            if attempt == 0:
                if self.uses_api_token and resp.status_code in (401, 403):
                    # The token was rejected (expired, disabled, wrong scope or
                    # an older panel without token support). Drop it and fall
                    # back to the session login for this and every later call.
                    logger.warning(
                        "Panel API token rejected (HTTP %s) — falling back to "
                        "password login",
                        resp.status_code,
                    )
                    self.api_token = ""
                    self._invalidate_csrf()
                    try:
                        await self.login()
                    except Exception as exc:  # pragma: no cover - network
                        logger.warning("Panel fallback login failed: %s", exc)
                    continue
                if not self.uses_api_token:
                    if resp.status_code in (401, 419):
                        # Session expired — log in again and retry once.
                        try:
                            await self.login()
                        except Exception as exc:  # pragma: no cover - network
                            logger.warning("Panel re-login failed: %s", exc)
                        continue
                    if resp.status_code == 403:
                        # Stale CSRF token — mint a fresh one and retry once.
                        self._invalidate_csrf()
                        continue

            resp.raise_for_status()
            try:
                data = resp.json()
            except ValueError as exc:
                raise XUIError(
                    f"Panel returned non-JSON for {error_label} "
                    f"(HTTP {resp.status_code})"
                ) from exc

            if not data.get("success"):
                msg = str(data.get("msg") or "")
                if attempt == 0 and not self.uses_api_token and _msg_is_stale_csrf(msg):
                    self._invalidate_csrf()
                    continue
                raise XUIError(msg or f"Failed to {error_label}")
            return data

        raise XUIError(f"Failed to {error_label}")

    async def _get(self, path: str, *, error_label: str = "request") -> dict[str, Any]:
        return await self._request("GET", path, error_label=error_label)

    async def _post_panel(
        self,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        error_label: str = "request",
    ) -> dict[str, Any]:
        return await self._request(
            "POST", path, json=payload, error_label=error_label
        )

    _post = _post_panel

    # --- panel metadata ----------------------------------------------------

    async def fetch_settings(self) -> dict[str, Any]:
        """Read every panel setting (``POST /panel/api/setting/all``)."""
        data = await self._post(
            "/panel/api/setting/all", None, error_label="fetch panel settings"
        )
        return data.get("obj") or {}

    async def get_subscription_base_url(self) -> str:
        return resolve_subscription_base_url(await self.fetch_settings(), self.base_url)

    async def test_connection(self) -> dict[str, Any]:
        """Return a short summary of the panel state."""
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
            "auth_mode": "api_token" if self.uses_api_token else "password",
        }

    # --- API tokens --------------------------------------------------------

    async def list_api_tokens(self) -> list[dict[str, Any]]:
        """List the panel's API tokens (metadata only — never the value)."""
        data = await self._get(
            "/panel/api/setting/apiTokens", error_label="list API tokens"
        )
        obj = data.get("obj") or []
        return obj if isinstance(obj, list) else []

    async def create_api_token(
        self,
        name: str,
        *,
        scope: str = API_TOKEN_SCOPE_ADMIN,
        expires_at: int = 0,
    ) -> str:
        """
        Mint an API token and return its plaintext (shown only once).

        The panel stores a SHA-256 hash, so the caller has to persist the
        plaintext immediately.
        """
        data = await self._post(
            "/panel/api/setting/apiTokens/create",
            {"name": name, "scope": scope, "expiresAt": int(expires_at)},
            error_label="create API token",
        )
        obj = data.get("obj") or {}
        token = obj.get("token") or obj.get("value") or obj.get("apiToken")
        if not token:
            raise XUIError("Panel did not return an API token")
        return str(token)

    # --- inbounds ----------------------------------------------------------

    async def list_inbounds(self) -> list[dict[str, Any]]:
        data = await self._get("/panel/api/inbounds/list", error_label="list inbounds")
        obj = data.get("obj") or []
        return obj if isinstance(obj, list) else []

    # --- clients -----------------------------------------------------------

    async def get_client(self, email: str) -> Optional[dict[str, Any]]:
        """
        Fetch one client. Returns None only when the panel explicitly reports it
        is missing; any other failure raises XUIError so a transient panel error
        can never be mistaken for "client does not exist" (which would create a
        duplicate client).

        The panel answers a missing client with ``success: false`` and a
        "record not found" message rather than a 404, so the message decides.
        """
        try:
            data = await self._get(
                f"/panel/api/clients/get/{email}", error_label=f"fetch client {email}"
            )
        except XUIError as exc:
            if _msg_is_not_found(str(exc)):
                return None
            raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        obj = data.get("obj")
        if obj is None:
            return None
        return obj

    async def get_client_traffic(self, email: str) -> ClientTraffic:
        """Read the up/down counters for one client."""
        data = await self._get(
            f"/panel/api/clients/traffic/{email}",
            error_label=f"fetch traffic for {email}",
        )
        obj = data.get("obj") or {}
        if not isinstance(obj, dict):
            return ClientTraffic()
        return ClientTraffic(
            up=int(obj.get("up") or 0),
            down=int(obj.get("down") or 0),
            total=int(obj.get("total") or 0),
        )

    async def list_clients(self) -> list[dict[str, Any]]:
        """
        List every client on the panel with its inbound ids and traffic.

        One request replaces the previous "one ``clients/get`` call per user"
        sweep, which mattered on a panel with ~90 clients.
        """
        data = await self._get("/panel/api/clients/list", error_label="list clients")
        obj = data.get("obj") or []
        return obj if isinstance(obj, list) else []

    async def add_client(
        self,
        email: str,
        inbound_ids: list[int],
        total_bytes: int,
        expiry_ms: int,
        comment: str,
        *,
        limit_ip: int = DEFAULT_CLIENT_LIMIT_IP,
    ) -> dict[str, Any]:
        payload = {
            "client": {
                "email": email,
                "enable": True,
                "totalGB": total_bytes,
                "expiryTime": expiry_ms,
                "limitIp": int(limit_ip),
                "tgId": 0,
                "comment": comment,
            },
            "inboundIds": inbound_ids,
        }
        await self._post(
            "/panel/api/clients/add", payload, error_label="add client"
        )
        detail = await self.get_client(email)
        if not detail:
            raise XUIError("Client created but could not be fetched")
        return detail

    async def sync_client_inbounds(
        self,
        email: str,
        inbound_ids: list[int],
    ) -> dict[str, Any]:
        """Attach missing inbounds and detach ones no longer selected."""
        detail = await self.get_client(email)
        if not detail:
            raise XUIError(f"Client {email} not found")

        current_ids = set(detail.get("inboundIds") or [])
        desired_ids = set(inbound_ids)
        missing = sorted(desired_ids - current_ids)
        extra = sorted(current_ids - desired_ids)

        if missing:
            await self._post(
                f"/panel/api/clients/{email}/attach",
                {"inboundIds": missing},
                error_label=f"attach client {email} to inbounds {missing}",
            )

        if extra:
            await self._post(
                f"/panel/api/clients/{email}/detach",
                {"inboundIds": extra},
                error_label=f"detach client {email} from inbounds {extra}",
            )

        detail = await self.get_client(email)
        if not detail:
            raise XUIError("Client inbound sync succeeded but client could not be fetched")
        return detail

    async def update_client(
        self,
        email: str,
        client_payload: dict[str, Any],
        inbound_ids: list[int],
    ) -> dict[str, Any]:
        """
        Replace a client record.

        The panel replaces the whole row, so ``client_payload`` must be a
        complete record — see :func:`prepare_client_update_payload`.
        """
        await self._post(
            f"/panel/api/clients/update/{email}",
            client_payload,
            error_label="update client",
        )
        return await self.sync_client_inbounds(email, inbound_ids)

    async def bulk_adjust(
        self,
        emails: list[str],
        *,
        add_days: int = 0,
        add_bytes: int = 0,
    ) -> dict[str, Any]:
        """
        Shift expiry and/or quota for many clients in one call.

        ``POST /panel/api/clients/bulkAdjust`` applies addDays/addBytes as
        deltas in one server-side read-modify-write, so there is no
        client-side read-modify-write race. The panel also re-enables a client
        that was auto-disabled solely because it was depleted once the
        adjustment lifts it out of depletion, and skips the expiry/traffic
        field when it is unlimited (0) instead of narrowing it.

        Returns ``{"adjusted": int, "skipped": [{"email", "reason"}, ...]}``.
        """
        if not emails:
            return {"adjusted": 0, "skipped": []}
        if add_days == 0 and add_bytes == 0:
            raise ValueError("no adjustment specified")

        payload: dict[str, Any] = {
            "emails": list(emails),
            "addDays": int(add_days),
            "addBytes": int(add_bytes),
        }

        data = await self._post(
            "/panel/api/clients/bulkAdjust",
            payload,
            error_label="bulk-adjust clients",
        )
        obj = data.get("obj") or {}
        if not isinstance(obj, dict):
            raise XUIError("Unexpected bulkAdjust payload")
        return obj

    async def enforce_limit_ip(
        self,
        email: str,
        *,
        limit_ip: int = DEFAULT_CLIENT_LIMIT_IP,
    ) -> dict[str, Any]:
        """Set limitIp on an existing client without changing quota/expiry."""
        detail = await self.get_client(email)
        if not detail or not detail.get("client"):
            raise XUIError(f"Client {email} not found")
        client = detail["client"]
        payload = prepare_client_update_payload(
            detail,
            email=email,
            limit_ip=limit_ip,
        )
        inbound_ids = [int(i) for i in (detail.get("inboundIds") or [])]
        return await self.update_client(email, payload, inbound_ids)


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
    Build the subscription base URL from the panel settings.

    The panel serves subscriptions from ``subPath`` on ``subPort`` (optionally
    on a dedicated ``subDomain``), or from an explicit ``subURI`` override.
    The result ends with ``/`` — append the client's subId for the full link.
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
