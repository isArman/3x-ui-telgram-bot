import os
from pathlib import Path
from typing import List


def get_env(name: str, default: str = "") -> str:
    """Read env var, tolerating accidental leading/trailing spaces in key names."""
    direct = os.getenv(name)
    if direct is not None:
        return direct.strip()

    target = name.strip()
    for key, value in os.environ.items():
        if key.strip() == target:
            return value.strip()

    return default


def _parse_admin_ids(value: str) -> List[int]:
    if not value:
        return []
    return [int(part.strip()) for part in value.split(",") if part.strip().isdigit()]


def _parse_bool(value: str, default: bool = True) -> bool:
    if not value:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


class Settings:
    BASE_DIR: Path = Path(__file__).parent.parent.parent
    DATA_DIR: Path = BASE_DIR / "data"

    @property
    def BOT_TOKEN(self) -> str:
        return get_env("BOT_TOKEN")

    @property
    def DATABASE_URL(self) -> str:
        return get_env("DATABASE_URL", "sqlite+aiosqlite:///data/db.sqlite3")

    @property
    def CARD_NUMBER(self) -> str:
        return get_env("CARD_NUMBER")

    @property
    def CARD_HOLDER(self) -> str:
        return get_env("CARD_HOLDER")

    @property
    def ADMIN_IDS(self) -> List[int]:
        return _parse_admin_ids(get_env("ADMIN_IDS"))

    @property
    def SECRET_KEY(self) -> str:
        """Used to encrypt panel credentials at rest. Generate with Fernet.generate_key()."""
        return get_env("SECRET_KEY")

    @property
    def XUI_VERIFY_SSL(self) -> bool:
        """Verify TLS certificates when connecting to 3x-ui panel."""
        return _parse_bool(get_env("XUI_VERIFY_SSL", "true"), default=True)

    @property
    def LOG_LEVEL(self) -> str:
        return get_env("LOG_LEVEL", "INFO").upper()

    def validate(self):
        required = {
            "BOT_TOKEN": self.BOT_TOKEN,
            "ADMIN_IDS": self.ADMIN_IDS,
            "CARD_NUMBER": self.CARD_NUMBER,
            "CARD_HOLDER": self.CARD_HOLDER,
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            raise ValueError(f"Missing required settings: {', '.join(missing)}")


settings = Settings()
