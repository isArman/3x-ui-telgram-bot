from app.services.traffic_usage import (
    GB,
    is_low_traffic,
    parse_client_traffic,
    remaining_traffic_percent,
)


def test_parse_client_traffic():
    detail = {
        "client": {"totalGB": 10 * GB},
        "traffic": {"up": 2 * GB, "down": 3 * GB},
    }
    assert parse_client_traffic(detail) == (10 * GB, 5 * GB)


def test_parse_client_traffic_unlimited():
    detail = {"client": {"totalGB": 0}, "traffic": {"up": 999, "down": 999}}
    assert parse_client_traffic(detail) is None


def test_remaining_percent():
    total = 10 * GB
    used = 9 * GB
    assert remaining_traffic_percent(total, used) == 10.0
    assert is_low_traffic(total, used) is False

    used_low = int(9.1 * GB)
    assert remaining_traffic_percent(total, used_low) < 10.0
    assert is_low_traffic(total, used_low) is True
