# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| main    | yes       |

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security bugs.

Contact the repository maintainer privately with:

- Description of the issue
- Steps to reproduce
- Impact assessment

## Security practices for operators

### Secrets

- Never commit `.env`, `data/`, or `app/config/plans.yaml`
- Rotate `BOT_TOKEN` via BotFather `/revoke` if exposed
- Set a strong random `SECRET_KEY` in production (see `.env.example`)
- Rotate 3x-ui panel password if it was shared

### Data stored by the bot

| Data | Location | Notes |
|------|----------|-------|
| Bot token | `.env` | Required to run |
| Panel password | SQLite (`panel_settings`) | Encrypted at rest when `SECRET_KEY` is set |
| Payment receipts | Telegram + `payments.receipt_file_id` | Telegram file reference |
| Subscription URLs | SQLite | Required for service delivery |
| User profile | SQLite | Telegram id, username, name |

### 3x-ui panel

- Panel credentials are entered in Telegram admin chat — delete sensitive messages after setup
- Use `XUI_VERIFY_SSL=true` in production
- Only trusted admin Telegram IDs should be in `ADMIN_IDS`

### Privacy

- 3x-ui client **comment** field stores Telegram user id, username, and display name only
- Logs should not contain tokens or passwords (report if you find any)

## Known limitations

- FSM state uses in-memory storage (lost on restart)
- Rate limiting is per-process (not suitable for multi-instance without Redis)
- Manual payment approval requires human review of receipt photos
