"""Traffic usage helpers for low-volume notifications.

The 3x-ui panel reports a client's traffic in two shapes, and the bot has to
cope with both:

* ``GET /panel/api/clients/get/:email`` returns an envelope
  ``{client, inboundIds, usedTraffic, ...}`` where ``usedTraffic`` is the
  *total* bytes used (up + down).
* ``GET /panel/api/clients/list`` and ``GET /panel/api/clients/traffic/:email``
  return a ``traffic: {up, down, ...}`` object.

Older panels only ever returned the ``traffic`` object, so both are accepted.
"""

from __future__ import annotations

from typing import Any

GB = 1024**3
LOW_TRAFFIC_THRESHOLD_PERCENT = 10.0


def client_record(detail: dict[str, Any]) -> dict[str, Any]:
    """The client record, whether it is wrapped in an envelope or flat."""
    inner = detail.get("client")
    return inner if isinstance(inner, dict) else detail


def used_bytes(detail: dict[str, Any]) -> int:
    """Bytes consumed by the client, from whichever shape the panel returned."""
    traffic = detail.get("traffic")
    if isinstance(traffic, dict):
        up = traffic.get("up")
        down = traffic.get("down")
        if up is not None or down is not None:
            return int(up or 0) + int(down or 0)
    used = detail.get("usedTraffic")
    if used is not None:
        return int(used or 0)
    return 0


def parse_client_traffic(detail: dict[str, Any]) -> tuple[int, int] | None:
    """
    Parse a client payload.

    Returns (total_bytes, used_bytes) or None when the quota is unlimited.
    """
    record = client_record(detail)
    total_bytes = int(record.get("totalGB") or 0)
    if total_bytes <= 0:
        return None
    return total_bytes, used_bytes(detail)


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
