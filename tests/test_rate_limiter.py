import pytest
import asyncio
from datetime import datetime, timedelta
from app.utils.rate_limiter import RateLimiter


def test_rate_limiter_allows_first_action():
    """Test that first action is always allowed"""
    limiter = RateLimiter()
    can_proceed, remaining = limiter.check_limit(user_id=123, action="test", seconds=60)
    
    assert can_proceed is True
    assert remaining is None


def test_rate_limiter_blocks_rapid_actions():
    """Test that rapid actions are blocked"""
    limiter = RateLimiter()
    
    # First action should succeed
    can_proceed, remaining = limiter.check_limit(user_id=123, action="test", seconds=60)
    assert can_proceed is True
    
    # Immediate second action should be blocked
    can_proceed, remaining = limiter.check_limit(user_id=123, action="test", seconds=60)
    assert can_proceed is False
    assert remaining is not None
    assert remaining > 0


def test_rate_limiter_allows_after_timeout():
    """Test that actions are allowed after timeout"""
    limiter = RateLimiter()
    
    # First action
    can_proceed, remaining = limiter.check_limit(user_id=123, action="test", seconds=1)
    assert can_proceed is True
    
    # Wait for timeout
    import time
    time.sleep(1.1)
    
    # Second action should succeed
    can_proceed, remaining = limiter.check_limit(user_id=123, action="test", seconds=1)
    assert can_proceed is True


def test_rate_limiter_different_users():
    """Test that different users have independent limits"""
    limiter = RateLimiter()
    
    # User 1 action
    can_proceed, _ = limiter.check_limit(user_id=123, action="test", seconds=60)
    assert can_proceed is True
    
    # User 2 action (different user, should succeed)
    can_proceed, _ = limiter.check_limit(user_id=456, action="test", seconds=60)
    assert can_proceed is True


def test_rate_limiter_different_actions():
    """Test that different actions have independent limits"""
    limiter = RateLimiter()
    
    # Action 1
    can_proceed, _ = limiter.check_limit(user_id=123, action="create_order", seconds=60)
    assert can_proceed is True
    
    # Action 2 (different action, should succeed)
    can_proceed, _ = limiter.check_limit(user_id=123, action="send_message", seconds=60)
    assert can_proceed is True


def test_rate_limiter_reset():
    """Test that reset clears the limit"""
    limiter = RateLimiter()
    
    # First action
    can_proceed, _ = limiter.check_limit(user_id=123, action="test", seconds=60)
    assert can_proceed is True
    
    # Reset
    limiter.reset_user(user_id=123, action="test")
    
    # Second action should succeed
    can_proceed, _ = limiter.check_limit(user_id=123, action="test", seconds=60)
    assert can_proceed is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
