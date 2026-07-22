# Contributing

Thank you for contributing to this project!

## Development setup

```bash
git clone https://github.com/isArman/3x-ui-telgram-bot.git
cd 3x-ui-telgram-bot
cp .env.example .env
cp app/config/plans.example.yaml app/config/plans.yaml
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r tests/requirements.txt
python -m pytest tests/ -v
```

## Pull requests

1. Fork the repository
2. Create a feature branch
3. Keep changes focused — one concern per PR
4. Run tests: `python -m pytest tests/`
5. Do not commit secrets, `.env`, or `data/`

## Code style

- Match existing patterns in handlers and services
- User-facing text in Persian (`app/config/texts.py`)
- Security-sensitive values via `app/config/settings.py`

## Reporting bugs

Open a GitHub issue with steps to reproduce, expected vs actual behavior, and logs (redact tokens).
