# Telegram VPN Shop Bot

A Telegram bot for selling VPN subscriptions with manual config inventory. Built with Python, Aiogram 3, and SQLAlchemy.

## Features

- Pre-defined VPN plans with custom pricing
- Custom plan creation (user-defined days and traffic)
- Payment receipt upload and admin review
- Pre-stocked configs per plan — auto-sent on payment approval
- SQLite database with async support
- Docker-ready with docker-compose

## Quick Start

```bash
git clone <repository-url>
cd 3x-ui-telgram-bot
cp .env.example .env
```

Edit `.env`:

```env
BOT_TOKEN=your_telegram_bot_token
ADMIN_IDS=123456789
CARD_NUMBER=6037-9971-1234-5678
CARD_HOLDER=John Doe
```

```bash
docker-compose up -d --build
```

## Admin

Admins see **⚙️ پنل ادمین** in the main menu, or use:

| Command | Description |
|---------|-------------|
| `/admin` | Admin panel menu |
| `/configs` | Manage configs per plan |
| `/dashboard` | Stats and inventory |
| `/pending` | Pending payments |

### Config workflow

1. Create VPN configs manually outside the bot
2. Open **⚙️ پنل ادمین** → **مدیریت کانفیگ‌ها**
3. Add subscription links or `vless://` configs to each plan
4. Approve payments — next free config is sent to the user

## Plans

Edit `app/config/plans.yaml` to change plans and pricing.

## License

MIT
