import os
from pathlib import Path
from typing import List


class Settings:
    # Bot
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    
    @property
    def ADMIN_IDS(self) -> List[int]:
        admin_ids_str = os.getenv("ADMIN_IDS", "")
        if not admin_ids_str:
            return []
        return [int(x.strip()) for x in admin_ids_str.split(",") if x.strip().isdigit()]

    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/db.sqlite3")

    # 3x-ui Panel
    XUI_URL: str = os.getenv("XUI_URL", "")
    XUI_PUBLIC_URL: str = os.getenv("XUI_PUBLIC_URL", "")
    XUI_USERNAME: str = os.getenv("XUI_USERNAME", "")
    XUI_PASSWORD: str = os.getenv("XUI_PASSWORD", "")
    XUI_INBOUND_ID: int = int(os.getenv("XUI_INBOUND_ID", "1"))
    PROVISION_MODE: str = os.getenv("PROVISION_MODE", "direct")  # direct | remote

    # Remote worker API (bot server exposes this; Iran worker polls it)
    WORKER_SECRET: str = os.getenv("WORKER_SECRET", "")
    WORKER_API_HOST: str = os.getenv("WORKER_API_HOST", "0.0.0.0")
    WORKER_API_PORT: int = int(os.getenv("WORKER_API_PORT", "8080"))

    # Remote worker client (runs on Iran server next to 3x-ui)
    BOT_API_URL: str = os.getenv("BOT_API_URL", "")

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
            "CARD_NUMBER": cls.CARD_NUMBER,
            "CARD_HOLDER": cls.CARD_HOLDER,
        }
        
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise ValueError(f"Missing required settings: {', '.join(missing)}")


settings = Settings()
