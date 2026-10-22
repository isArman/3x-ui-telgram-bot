from app.services.referral import apply_purchase_discount


def test_apply_purchase_discount_fifteen_percent():
    payable, original, applied = apply_purchase_discount(100_000)
    assert original == 100_000
    assert payable == 85_000
    assert applied is True


def test_apply_purchase_discount_keeps_at_least_one_toman():
    payable, _original, applied = apply_purchase_discount(1)
    assert payable == 1
    assert applied is False
