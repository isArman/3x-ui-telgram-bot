import pytest
from datetime import datetime, timedelta

from app.services.renewal import compute_extended_expiry


def test_extend_from_future_expiry():
    current = datetime.utcnow() + timedelta(days=10)
    result = compute_extended_expiry(current, 30)
    assert result == current + timedelta(days=30)


def test_extend_from_expired_starts_now():
    current = datetime.utcnow() - timedelta(days=5)
    before = datetime.utcnow()
    result = compute_extended_expiry(current, 30)
    assert result >= before + timedelta(days=29)
    assert result <= before + timedelta(days=31)
