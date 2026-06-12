# Bot Enhancements

## New Features

### 1. Logging System
- Comprehensive logging to `data/bot.log`
- Console and file output
- Tracks all user actions and errors

### 2. Anti-Spam Rate Limiting
- Order creation limited to 1 per 60 seconds per user
- Prevents spam and abuse
- User-friendly error messages

### 3. Order History
- Users can view all their past orders
- Shows order status, dates, and details
- Access via "📋 سفارش‌های من" button

### 4. Account Management
- Users can view all their VPN accounts
- Shows expiry dates, traffic limits, status
- Displays subscription links
- Access via "💳 اکانت‌های من" button

### 5. Admin Dashboard
- Command: `/dashboard`
- Shows:
  - Total users, orders, active accounts
  - Pending payments count
  - Revenue statistics (today, week, month, total)
  - Accounts expiring soon

### 6. Bulk Admin Actions
- Command: `/pending` - View all pending payments
- Command: `/payments` - View payment history (last 20)
- Easier payment management for admins

### 7. Notification System
- Automatic notifications for accounts expiring in 3 days
- Runs every 6 hours in background
- Users get reminded to renew their accounts

### 8. Better Error Handling
- Try-catch blocks on all handlers
- User-friendly error messages
- Detailed error logging for debugging

## Admin Commands

- `/dashboard` - View statistics dashboard
- `/pending` - List pending payments
- `/payments` - View payment history

## Database Changes

New fields added to `VPNAccount` table:
- `expiry_notified` - Tracks if expiry notification was sent
- `last_renewed_at` - Tracks renewal dates
- `updated_at` - Tracks last update time

## Technical Details

### New Files
- `app/utils/logger.py` - Logging configuration
- `app/utils/rate_limiter.py` - Rate limiting implementation
- `app/utils/statistics.py` - Dashboard statistics helper
- `app/utils/notifications.py` - Notification system
- `app/utils/scheduler.py` - Background task scheduler

### Modified Files
- `app/database/models.py` - Added new fields
- `app/bot/handlers/user.py` - Added account management, logging, rate limiting
- `app/bot/handlers/admin.py` - Added dashboard and bulk actions
- `app/bot/keyboards/user.py` - Added new buttons
- `app/core/runner.py` - Integrated notification scheduler

## Setup

No additional configuration needed. All features work automatically after rebuilding:

```bash
sudo docker-compose up -d --build
```

## Logs

View logs in real-time:
```bash
sudo docker logs -f vpn_bot
```

Or check the log file inside container:
```bash
sudo docker exec vpn_bot cat data/bot.log
```
