import json
import secrets
import uuid as uuid_lib
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import httpx

from app.utils.logger import logger


class XUIClient:
    """3x-ui panel API client (https://github.com/MHSanaei/3x-ui/wiki)."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        inbound_id: int = 1,
    ):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.inbound_id = inbound_id
        self.session_cookie: Optional[str] = None

    async def _get_headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.session_cookie:
            headers["Cookie"] = self.session_cookie
        return headers

    async def login(self) -> bool:
        """Login to 3x-ui panel and store session cookie."""
        if not self.base_url or not self.username or not self.password:
            return False

        try:
            async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
                response = await client.post(
                    f"{self.base_url}/login",
                    data={
                        "username": self.username,
                        "password": self.password,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )

                if response.status_code != 200:
                    logger.error(f"XUI login failed: HTTP {response.status_code}")
                    return False

                data = response.json()
                if not data.get("success"):
                    logger.error(f"XUI login failed: {data.get('msg', 'unknown error')}")
                    return False

                if response.cookies:
                    self.session_cookie = "; ".join(
                        f"{key}={value}" for key, value in response.cookies.items()
                    )
                return True
        except Exception as exc:
            logger.error(f"XUI login error: {exc}")
            return False

    async def _ensure_logged_in(self) -> bool:
        if not self.session_cookie:
            return await self.login()
        return True

    async def test_connection(self) -> tuple[bool, str]:
        """Test panel login and optionally verify inbound exists."""
        if not await self.login():
            return False, "ورود به پنل ناموفق بود. URL، نام کاربری یا رمز عبور را بررسی کنید."

        try:
            async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
                response = await client.get(
                    f"{self.base_url}/panel/api/inbounds/list",
                    headers=await self._get_headers(),
                )

                if response.status_code != 200:
                    return False, f"دریافت inboundها ناموفق بود (HTTP {response.status_code})."

                data = response.json()
                if not data.get("success"):
                    return False, data.get("msg", "خطا در دریافت inboundها.")

                inbounds = data.get("obj") or []
                inbound_ids = [item.get("id") for item in inbounds]
                if self.inbound_id not in inbound_ids:
                    return False, f"Inbound با شناسه {self.inbound_id} یافت نشد."

                return True, f"اتصال موفق. Inbound #{self.inbound_id} فعال است."
        except Exception as exc:
            logger.error(f"XUI test connection error: {exc}")
            return False, f"خطا در تست اتصال: {exc}"

    async def add_client(
        self,
        email: str,
        traffic_gb: int,
        expire_days: int,
        client_uuid: Optional[str] = None,
        sub_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Add a client to the configured inbound.
        API: POST /panel/api/inbounds/addClient
        """
        if not await self._ensure_logged_in():
            return None

        expiry_time = int(
            (datetime.utcnow() + timedelta(days=expire_days)).timestamp() * 1000
        )
        total_bytes = traffic_gb * 1024 * 1024 * 1024
        client_uuid = client_uuid or str(uuid_lib.uuid4())
        sub_id = sub_id or secrets.token_hex(8)

        client_settings = {
            "clients": [
                {
                    "id": client_uuid,
                    "email": email,
                    "limitIp": 0,
                    "totalGB": total_bytes,
                    "expiryTime": expiry_time,
                    "enable": True,
                    "tgId": "",
                    "subId": sub_id,
                    "flow": "",
                    "reset": 0,
                }
            ]
        }

        payload = {
            "id": self.inbound_id,
            "settings": json.dumps(client_settings),
        }

        try:
            async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
                response = await client.post(
                    f"{self.base_url}/panel/api/inbounds/addClient",
                    data=payload,
                    headers=await self._get_headers(),
                )

                if response.status_code != 200:
                    logger.error(f"XUI add client failed: HTTP {response.status_code}")
                    return None

                data = response.json()
                if not data.get("success"):
                    logger.error(f"XUI add client failed: {data.get('msg', response.text)}")
                    return None

                return {
                    "email": email,
                    "uuid": client_uuid,
                    "sub_id": sub_id,
                    "expiry_time": expiry_time,
                    "traffic_gb": traffic_gb,
                    "subscription_url": self.build_subscription_url(sub_id),
                }
        except Exception as exc:
            logger.error(f"XUI add client error: {exc}")
            return None

    def build_subscription_url(self, sub_id: str) -> str:
        return f"{self.base_url}/sub/{sub_id}"

    async def get_client_traffic(self, email: str) -> Optional[Dict[str, Any]]:
        """Get client traffic stats."""
        if not await self._ensure_logged_in():
            return None

        try:
            async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
                response = await client.get(
                    f"{self.base_url}/panel/api/inbounds/getClientTraffics/{email}",
                    headers=await self._get_headers(),
                )

                if response.status_code == 200:
                    data = response.json()
                    if data.get("success"):
                        return data.get("obj")
        except Exception as exc:
            logger.error(f"XUI get client traffic error: {exc}")

        return None

    async def delete_client(self, client_uuid: str) -> bool:
        """Delete a client from the configured inbound."""
        if not await self._ensure_logged_in():
            return False

        try:
            async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
                response = await client.post(
                    f"{self.base_url}/panel/api/inbounds/{self.inbound_id}/delClient/{client_uuid}",
                    headers=await self._get_headers(),
                )

                if response.status_code == 200:
                    data = response.json()
                    return data.get("success", False)
        except Exception as exc:
            logger.error(f"XUI delete client error: {exc}")

        return False
