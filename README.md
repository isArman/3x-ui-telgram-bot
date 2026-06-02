 # Telegram VPN Bot (3x-ui Integration)
 
 A production-ready Telegram bot for selling VPN accounts via 3x-ui panel. Built with Python, Aiogram 3, and SQLAlchemy.
 
 ## Features
 
 - 📦 Pre-defined VPN plans with custom pricing
 - 🎨 Custom plan creation (user-defined days and traffic)
 - 💳 Payment receipt upload and admin review
 - ✅ Automatic VPN account creation on approval
 - 🔒 SQLite database with async support
 - 🐳 Docker-ready with docker-compose
 - 🔄 Restart-safe stateless design
 
 ## Quick Start
 
 ### 1. Clone and Configure
 
 ```bash
 git clone <repository-url>
 cd 3x-ui-shop
 cp .env.example .env
 ```
 
 Edit `.env` with your credentials:
 
 ```env
 BOT_TOKEN=your_telegram_bot_token
 ADMIN_IDS=123456789,987654321
 XUI_URL=https://your-panel.example.com
 XUI_USERNAME=admin
 XUI_PASSWORD=password
 CARD_NUMBER=6037-9971-1234-5678
 CARD_HOLDER=John Doe
 ```
 
 ### 2. Run with Docker
 
 ```bash
 docker-compose up -d
 ```
 
 ### 3. Check Logs
 
 ```bash
 docker-compose logs -f bot
 ```
 
 ## Project Structure
 
 ```
 app/
 ├── bot/
 │   ├── handlers/          # User and admin handlers
 │   ├── keyboards/         # Telegram keyboards
 │   └── states/            # FSM states
 ├── config/
 │   ├── settings.py        # Environment configuration
 │   ├── plans.yaml         # VPN plans definition
 │   └── texts.py           # Bot messages (Persian)
 ├── database/
 │   ├── base.py            # SQLAlchemy base
 │   ├── session.py         # DB session management
 │   └── models.py          # Database models
 ├── xui/
 │   └── client.py          # 3x-ui API client
 ├── core/
 │   └── runner.py          # Bot runner
 └── main.py                # Entry point
 ```
 
 ## Database Schema
 
 - **users** - Telegram user information
 - **orders** - VPN plan orders
 - **payments** - Payment receipts and status
 - **vpn_accounts** - Created VPN accounts
 
 ## User Flow
 
 1. User selects a plan or creates custom plan
 2. Order is created with payment instructions
 3. User uploads payment receipt
 4. Admin reviews and approves/rejects payment
 5. On approval, VPN account is automatically created
 6. User receives subscription link
 
 ## Admin Flow
 
 Admins receive payment notifications with:
 - User information
 - Order details
 - Payment receipt
 - Approve/Reject buttons
 
 ## Customization
 
 ### Edit Plans
 
 Edit `app/config/plans.yaml`:
 
 ```yaml
 plans:
   - id: basic
     name: "پلن پایه"
     days: 30
     traffic: 50
     price: 100000
 
 pricing:
   per_day: 1000    # Price per day
   per_gb: 3000     # Price per GB
 ```
 
 ### Edit Messages
 
 Edit `app/config/texts.py` to customize bot messages.
 
 ## Requirements
 
 - Python 3.11+
 - Docker & Docker Compose
 - 3x-ui panel with API access
 - Telegram Bot Token
 
 ## Development
 
 ### Without Docker
 
 ```bash
 python -m venv venv
 source venv/bin/activate
 pip install -r requirements.txt
 python -m app.main
 ```
 
 ## Notes
 
 - SQLite database is stored in `./data/db.sqlite3`
 - Bot uses FSM for multi-step flows
 - All timestamps are in UTC
 - VPN account email format: `tg_{telegram_id}`
 - Default inbound ID is 1 (configurable via `XUI_INBOUND_ID`)
 
 ## Future Enhancements
 
 Architecture supports:
 - Multiple server locations
 - PostgreSQL migration
 - Payment gateway integration
 - Auto-renewal system
 - Usage monitoring
 
 ## License
 
 MIT
