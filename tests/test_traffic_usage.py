"""Traffic parsing against the shapes the 3x-ui panel actually returns."""

import pytest

from app.services.traffic_usage import (
    GB,
    client_record,
    is_low_traffic,
    parse_client_traffic,
    remaining_traffic_percent,
    used_bytes,
)


def test_clients_get_envelope_uses_used_traffic():
    """`clients/get` returns `usedTraffic` (total bytes), not a traffic object."""
    detail = {
        "client": {"email": "1", "totalGB": 10 * GB, "expiryTime": 0},
        "inboundIds": [1],
        "usedTraffic": 4 * GB,
        "externalLinks": [],
        "tunnelAllowedIPs": {},
    }

    assert used_bytes(detail) == 4 * GB
    assert parse_client_traffic(detail) == (10 * GB, 4 * GB)


def test_clients_list_item_uses_traffic_object():
    """`clients/list` returns a flat client with `traffic: {up, down}`."""
    item = {
        "email": "1",
        "totalGB": 10 * GB,
        "traffic": {"up": 1 * GB, "down": 2 * GB},
    }

    assert used_bytes(item) == 3 * GB
    assert parse_client_traffic(item) == (10 * GB, 3 * GB)
    assert client_record(item)["email"] == "1"


def test_client_record_unwraps_the_envelope():
    detail = {"client": {"email": "7"}, "usedTraffic": 0}
    assert client_record(detail) == {"email": "7"}


def test_unlimited_quota_has_no_traffic_figures():
    detail = {"client": {"totalGB": 0}, "usedTraffic": 5}
    assert parse_client_traffic(detail) is None


def test_remaining_percent():
    total = 10 * GB
    used = 9 * GB
    assert remaining_traffic_percent(total, used) == pytest.approx(10.0)
    assert is_low_traffic(total, used) is False

    used_low = int(9.1 * GB)
    assert remaining_traffic_percent(total, used_low) == pytest.approx(9.0)
    assert is_low_traffic(total, used_low) is True
