import os
from pathlib import Path
from typing import List


def _parse_admin_ids(value: str) -> List[int]:
    if not value:
        return []
    return [int(part.strip()) for part in value.split(",") if part.strip().isdigit()]


class Settings:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/db.sqlite3")
    CARD_NUMBER: str = os.getenv("CARD_NUMBER", "")
    CARD_HOLDER: str = os.getenv("CARD_HOLDER", "")

    BASE_DIR: Path = Path(__file__).parent.parent.parent
    DATA_DIR: Path = BASE_DIR / "data"

    @property
    def ADMIN_IDS(self) -> List[int]:
        return _parse_admin_ids(os.getenv("ADMIN_IDS", ""))

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
