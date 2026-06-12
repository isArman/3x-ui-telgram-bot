# Testing Checklist

## Automated Tests ✅

- [x] Rate limiter tests (6 tests)
- [x] Logger tests (5 tests)
- [x] Statistics tests (3 tests)
- [x] Notification tests (4 tests)
- [x] Model tests (6 tests)
- [x] Integration tests (4 tests)

**Total: 28 automated tests**

## Manual Testing Checklist

### User Features

- [ ] `/start` command works
- [ ] "📦 خرید پلن" shows plans correctly
- [ ] "🎨 پلن سفارشی" accepts custom input
- [ ] Order confirmation works
- [ ] Payment instructions displayed
- [ ] Receipt upload works
- [ ] "📋 سفارش‌های من" shows order history
- [ ] "💳 اکانت‌های من" shows VPN accounts
- [ ] Account status shows correct expiry date
- [ ] Cancel button works

### Admin Features

- [ ] `/dashboard` shows statistics
- [ ] `/pending` lists pending payments
- [ ] `/payments` shows payment history
- [ ] Approve button creates VPN account
- [ ] Admin can send subscription link
- [ ] Reject button works
- [ ] Notifications sent to user
- [ ] Admin receives payment notifications

### Rate Limiting

- [ ] Creating multiple orders quickly shows rate limit message
- [ ] Wait 60 seconds, can create order again
- [ ] Different users can create orders simultaneously

### Notifications

- [ ] Accounts expiring in 3 days receive notification
- [ ] Notification sent only once per account
- [ ] Expired accounts don't get notification

### Logging

- [ ] `data/bot.log` file created
- [ ] User actions logged
- [ ] Errors logged with stack traces
- [ ] Admin actions logged

### Error Handling

- [ ] Invalid order input shows error message
- [ ] Database errors handled gracefully
- [ ] Network errors don't crash bot
- [ ] User-friendly error messages

## Performance Testing

- [ ] Bot responds within 2 seconds
- [ ] Database queries optimized
- [ ] No memory leaks after 100 orders
- [ ] Handles 10 concurrent users

## Security Testing

- [ ] Non-admin can't access admin commands
- [ ] SQL injection prevented (parameterized queries)
- [ ] Rate limiting prevents spam
- [ ] Sensitive data not logged

## Deployment Checklist

- [ ] All tests pass: `./run_tests.sh`
- [ ] `.env` file configured
- [ ] Docker builds successfully
- [ ] Database migrations applied
- [ ] Logs accessible
- [ ] Backups configured

## Post-Deployment Verification

- [ ] Bot online and responding
- [ ] Admin commands work
- [ ] Users can create orders
- [ ] Payments processed correctly
- [ ] Notifications working
- [ ] No errors in logs

## Test Commands

```bash
# Run all tests
./run_tests.sh

# Test specific feature
pytest tests/test_rate_limiter.py -v

# Test with coverage
pytest tests/ --cov=app --cov-report=html

# View coverage
open htmlcov/index.html

# Check logs
sudo docker logs vpn_bot -f

# Check inside container
sudo docker exec vpn_bot cat data/bot.log
```

## Sign-off

- [ ] All automated tests pass
- [ ] Manual testing complete
- [ ] No critical bugs
- [ ] Documentation updated
- [ ] Ready for production

**Tested by**: _______________  
**Date**: _______________  
**Status**: ⬜ Pass / ⬜ Fail
