from app.xui.client import prepare_client_update_payload


def test_prepare_client_update_payload_strips_numeric_db_id():
    existing = {
        "client": {
            "id": 42,
            "email": "5821190149",
            "subId": "abc123",
            "totalGB": 1000,
            "expiryTime": 123,
            "limitIp": 2,
            "tgId": 99,
        },
        "uuid": "11111111-1111-1111-1111-111111111111",
        "inboundIds": [3],
    }

    payload = prepare_client_update_payload(
        existing,
        email="5821190149",
        total_bytes=2000,
        expiry_ms=456,
        comment="test user",
    )

    assert payload["id"] == "11111111-1111-1111-1111-111111111111"
    assert payload["subId"] == "abc123"
    assert payload["totalGB"] == 2000
    assert payload["expiryTime"] == 456
    assert payload["comment"] == "test user"
    # Always enforce single-IP policy (do not preserve old limitIp=2).
    assert payload["limitIp"] == 1
    assert payload["tgId"] == 99


def test_prepare_client_update_payload_keeps_string_protocol_id():
    existing = {
        "client": {
            "id": "22222222-2222-2222-2222-222222222222",
            "email": "user@example.com",
            "subId": "xyz",
        }
    }

    payload = prepare_client_update_payload(
        existing,
        email="user@example.com",
        total_bytes=1,
        expiry_ms=2,
        comment="",
    )

    assert payload["id"] == "22222222-2222-2222-2222-222222222222"
    assert payload["limitIp"] == 1


def test_prepare_client_update_payload_limit_ip_override():
    existing = {"client": {"id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "email": "x"}}
    payload = prepare_client_update_payload(
        existing,
        email="x",
        total_bytes=0,
        expiry_ms=0,
        comment="",
        limit_ip=3,
    )
    assert payload["limitIp"] == 3
