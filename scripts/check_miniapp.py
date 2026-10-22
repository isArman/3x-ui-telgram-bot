"""End-to-end check of the deployed Mini App: signed launch data over HTTPS."""

import hashlib
import hmac
import json
import sys
import time
from urllib.parse import urlencode

import httpx

BOT_TOKEN = sys.argv[1] if len(sys.argv) > 1 else ""
BASE = sys.argv[2] if len(sys.argv) > 2 else "https://mcp.anaiis.ir/vpn"
USER_ID = int(sys.argv[3]) if len(sys.argv) > 3 else 45963071


def make_init_data(user_id: int) -> str:
    fields = {
        "auth_date": str(int(time.time())),
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
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(
        secret, check_string.encode(), hashlib.sha256
    ).hexdigest()
    return urlencode(fields)


def main() -> int:
    init_data = make_init_data(USER_ID)
    headers = {"X-Telegram-Init-Data": init_data}

    with httpx.Client(timeout=30.0, trust_env=False) as client:
        r = client.get(f"{BASE}/api/me", headers=headers)
        print("GET /api/me ->", r.status_code)
        data = r.json()
        if r.status_code != 200:
            print("   body:", str(data)[:300])
            return 1
        print("   user      :", data["user"])
        print("   balance   :", data["balance"])
        print("   referral  :", data["referral_code"], "|", data["referral_link"])
        print("   is_admin  :", data["is_admin"])
        print("   trial     :", data["trial"])
        print("   account   :", (data.get("account") or {}).get("expires_at"))

        r = client.get(f"{BASE}/api/plans", headers=headers)
        print("GET /api/plans ->", r.status_code)
        for p in r.json().get("plans", []):
            print(f"   {p['id']:10} {p['days']:>4}d {p['traffic']:>4}GB "
                  f"{p['devices']}dev  {p['price']:,}")

        r = client.post(
            f"{BASE}/api/quote",
            headers=headers,
            json={"plan_id": (r.json().get("plans") or [{}])[0].get("id", "basic")},
        )
        print("POST /api/quote ->", r.status_code, r.json())

        r = client.get(f"{BASE}/api/qr.png", headers=headers)
        print("GET /api/qr.png ->", r.status_code, r.headers.get("content-type"),
              len(r.content), "bytes")

        r = client.get(f"{BASE}/api/wallet", headers=headers)
        print("GET /api/wallet ->", r.status_code, "balance",
              r.json().get("balance"))

        r = client.get(f"{BASE}/api/referral", headers=headers)
        print("GET /api/referral ->", r.status_code, r.json().get("link"))

        r = client.get(f"{BASE}/api/admin/dashboard", headers=headers)
        print("GET /api/admin/dashboard ->", r.status_code,
              "(403 expected for a non-admin)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
