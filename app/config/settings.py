import os
from pathlib import Path
from typing import List


class Settings:
    # Bot
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    ADMIN_IDS: List[int] = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/db.sqlite3")

    # 3x-ui Panel
    XUI_URL: str = os.getenv("XUI_URL", "")
    XUI_USERNAME: str = os.getenv("XUI_USERNAME", "")
    XUI_PASSWORD: str = os.getenv("XUI_PASSWORD", "")
    XUI_INBOUND_ID: int = int(os.getenv("XUI_INBOUND_ID", "1"))

    # Payment
    CARD_NUMBER: str = os.getenv("CARD_NUMBER", "")
    CARD_HOLDER: str = os.getenv("CARD_HOLDER", "")

    # Paths
    BASE_DIR: Path = Path(__file__).parent.parent.parent
    DATA_DIR: Path = BASE_DIR / "data"

    @classmethod
    def validate(cls):
        """Validate required settings"""
        required = {
            "BOT_TOKEN": cls.BOT_TOKEN,
            "ADMIN_IDS": cls.ADMIN_IDS,
            "XUI_URL": cls.XUI_URL,
            "XUI_USERNAME": cls.XUI_USERNAME,
            "XUI_PASSWORD": cls.XUI_PASSWORD,
            "CARD_NUMBER": cls.CARD_NUMBER,
            "CARD_HOLDER": cls.CARD_HOLDER,
        }
        
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise ValueError(f"Missing required settings: {', '.join(missing)}")


settings = Settings()
