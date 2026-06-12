from datetime import datetime, timedelta
from typing import Dict, Optional


class RateLimiter:
    """Simple in-memory rate limiter for user actions"""
    
    def __init__(self):
        self._user_actions: Dict[int, Dict[str, datetime]] = {}
    
    def check_limit(
        self, 
        user_id: int, 
        action: str, 
        seconds: int = 60
    ) -> tuple[bool, Optional[int]]:
        """
        Check if user can perform action
        
        Returns:
            (can_proceed, seconds_remaining)
        """
        now = datetime.utcnow()
        
        if user_id not in self._user_actions:
            self._user_actions[user_id] = {}
        
        if action not in self._user_actions[user_id]:
            self._user_actions[user_id][action] = now
            return True, None
        
        last_action = self._user_actions[user_id][action]
        time_passed = (now - last_action).total_seconds()
        
        if time_passed >= seconds:
            self._user_actions[user_id][action] = now
            return True, None
        
        remaining = int(seconds - time_passed)
        return False, remaining
    
    def reset_user(self, user_id: int, action: Optional[str] = None):
        """Reset rate limit for user"""
        if user_id in self._user_actions:
            if action:
                self._user_actions[user_id].pop(action, None)
            else:
                self._user_actions.pop(user_id, None)


rate_limiter = RateLimiter()
