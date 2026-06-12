# VPN Bot Test Suite

## Overview

Comprehensive test suite for all bot features including logging, rate limiting, notifications, statistics, and database models.

## Test Structure

```
tests/
├── __init__.py
├── test_rate_limiter.py     # Rate limiting tests
├── test_logger.py            # Logging system tests
├── test_statistics.py        # Dashboard statistics tests
├── test_notifications.py     # Expiry notification tests
├── test_models.py            # Database model tests
├── test_integration.py       # End-to-end integration tests
├── requirements.txt          # Test dependencies
└── README.md                 # This file
```

## Running Tests

### Quick Start

```bash
./run_tests.sh
```

### Manual Test Run

```bash
# Install dependencies
pip install -r tests/requirements.txt

# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_rate_limiter.py -v

# Run with coverage
pytest tests/ --cov=app --cov-report=html
```

## Test Coverage

### 1. Rate Limiter Tests (`test_rate_limiter.py`)

- ✅ First action is allowed
- ✅ Rapid actions are blocked
- ✅ Actions allowed after timeout
- ✅ Different users have independent limits
- ✅ Different actions have independent limits
- ✅ Reset clears the limit

### 2. Logger Tests (`test_logger.py`)

- ✅ Logger creation
- ✅ File and console handlers exist
- ✅ Writes to log file correctly
- ✅ Creates directory if not exists
- ✅ Singleton pattern (same name returns same instance)

### 3. Statistics Tests (`test_statistics.py`)

- ✅ Dashboard stats with empty database
- ✅ Dashboard stats with sample data
- ✅ Detects accounts expiring soon
- ✅ Revenue calculations (today, week, month, total)
- ✅ Active vs expired account counting

### 4. Notification Tests (`test_notifications.py`)

- ✅ No notifications when no accounts expiring
- ✅ Notification sent for expiring account
- ✅ Already notified accounts skipped
- ✅ Expired accounts not notified
- ✅ Notification tracking (expiry_notified flag)

### 5. Model Tests (`test_models.py`)

- ✅ User creation
- ✅ Order creation
- ✅ Payment creation
- ✅ VPN account creation
- ✅ Notification tracking fields
- ✅ Default values and relationships

### 6. Integration Tests (`test_integration.py`)

- ✅ Complete order flow (user → order → payment → VPN account)
- ✅ Multiple orders per user
- ✅ Order rejection flow
- ✅ Account expiry status checking

## Test Database

Tests use SQLite in-memory database by default. Each test creates a fresh database ensuring isolation.

## Coverage Report

After running tests, view coverage report:

```bash
# Open HTML report
open htmlcov/index.html

# Or view in terminal
pytest tests/ --cov=app --cov-report=term-missing
```

## Expected Coverage

- **Rate Limiter**: 100%
- **Logger**: 95%+
- **Statistics**: 90%+
- **Notifications**: 90%+
- **Models**: 95%+
- **Integration**: 85%+

## CI/CD Integration

Add to your CI pipeline:

```yaml
# Example GitHub Actions
- name: Run tests
  run: |
    pip install -r tests/requirements.txt
    pytest tests/ --cov=app --cov-report=xml
    
- name: Upload coverage
  uses: codecov/codecov-action@v3
```

## Writing New Tests

### Template for new test file:

```python
import pytest
from app.your_module import your_function

def test_your_feature():
    """Test description"""
    result = your_function()
    assert result == expected_value

@pytest.mark.asyncio
async def test_async_feature():
    """Test async function"""
    result = await async_function()
    assert result is not None
```

### Best Practices

1. Use descriptive test names
2. One assertion per test when possible
3. Clean up test data
4. Use fixtures for common setup
5. Mock external dependencies (bot API calls)
6. Test edge cases and error conditions

## Troubleshooting

### Issue: Import errors

```bash
# Solution: Add project to PYTHONPATH
export PYTHONPATH=$PYTHONPATH:$(pwd)
pytest tests/
```

### Issue: Database locked

```bash
# Solution: Remove test database
rm -f data/test.db
pytest tests/
```

### Issue: Async warnings

```bash
# Solution: Install pytest-asyncio
pip install pytest-asyncio
```

## Manual Testing Checklist

In addition to automated tests, manually verify:

- [ ] Bot starts without errors
- [ ] User can view order history
- [ ] User can view accounts
- [ ] Admin dashboard shows correct stats
- [ ] Notifications sent at correct times
- [ ] Rate limiting prevents spam
- [ ] Logs written to file
- [ ] Error messages user-friendly
- [ ] All buttons work correctly
- [ ] Payment approval flow works

## Contact

For test issues or questions, check the logs in `data/bot.log`.
