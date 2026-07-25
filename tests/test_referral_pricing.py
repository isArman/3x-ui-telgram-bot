from app.services.referral import (
    apply_purchase_discount,
    format_price_block,
    preview_discounted_price,
)


def test_apply_purchase_discount_fifteen_percent():
    payable, original, applied = apply_purchase_discount(100_000)
    assert original == 100_000
    assert payable == 85_000
    assert applied is True


def test_preview_without_eligibility_keeps_list_price():
    payable, original, applied = preview_discounted_price(50_000, eligible=False)
    assert payable == original == 50_000
    assert applied is False


def test_format_price_block_shows_breakdown_when_discounted():
    text = format_price_block(100_000, payable=85_000, applied=True)
    assert "85,000" in text
    assert "100,000" in text
    assert "۱۵" in text or "15" in text
    assert "−15,000" in text or "-15,000" in text or "15,000" in text


def test_format_price_block_plain_when_no_discount():
    text = format_price_block(40_000, payable=40_000, applied=False)
    assert "40,000" in text
    assert "تخفیف" not in text
