"""Input validation helpers."""

from __future__ import annotations

from urllib.parse import urlparse

ALLOWED_CONFIG_PREFIXES = (
    "http://",
    "https://",
    "vless://",
    "vmess://",
    "trojan://",
    "ss://",
    "ssr://",
)

MAX_CONFIG_LENGTH = 4096


def is_valid_config_text(text: str) -> bool:
    """Validate subscription links or VPN config strings."""
    value = (text or "").strip()
    if not value or len(value) > MAX_CONFIG_LENGTH:
        return False
    if value.startswith(ALLOWED_CONFIG_PREFIXES):
        return True
    # Allow other opaque config blobs (e.g. base64) with minimum length
    return len(value) >= 20


def is_valid_subscription_base_url(url: str) -> bool:
    value = (url or "").strip().rstrip("/") + "/"
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)
