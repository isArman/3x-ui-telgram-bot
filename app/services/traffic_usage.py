"""Traffic usage helpers for low-volume notifications."""

from __future__ import annotations

from typing import Any, Optional

GB = 1024**3
LOW_TRAFFIC_THRESHOLD_PERCENT = 10.0


def parse_client_traffic(detail: dict[str, Any]) -> tuple[int, int] | None:
    """
    Parse 3x-ui GET /clients/get response.

    Returns (total_bytes, used_bytes) or None when traffic is unlimited.
    """
    client = detail.get("client") or {}
    total_bytes = int(client.get("totalGB") or 0)
    if total_bytes <= 0:
        return None

    traffic = detail.get("traffic") or {}
    used_bytes = int(traffic.get("up") or 0) + int(traffic.get("down") or 0)
    return total_bytes, used_bytes


def remaining_traffic_percent(total_bytes: int, used_bytes: int) -> float:
    """Remaining traffic as a percentage of the total limit."""
    if total_bytes <= 0:
        return 100.0
    remaining = max(total_bytes - used_bytes, 0)
    return (remaining / total_bytes) * 100.0


def is_low_traffic(total_bytes: int, used_bytes: int) -> bool:
    return remaining_traffic_percent(total_bytes, used_bytes) < LOW_TRAFFIC_THRESHOLD_PERCENT


def format_gb(bytes_value: int) -> str:
    return f"{bytes_value / GB:.2f}"
