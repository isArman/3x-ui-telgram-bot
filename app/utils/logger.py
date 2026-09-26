"""Application logging setup.

- Console + rotating file handler (bounded size, no unbounded bot.log growth).
- Level taken from LOG_LEVEL (default INFO).
- Idempotent: repeated setup_logger() calls for the same name return the same
  logger without stacking handlers.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config.settings import settings

_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 5 MB per file, keep 5 rotated files (~30 MB ceiling per logger).
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 5


def setup_logger(name: str = "vpn_bot", log_file: str = "data/bot.log") -> logging.Logger:
    """Create (or return) a configured logger with console + rotating file output."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    level = getattr(logging, settings.LOG_LEVEL, logging.INFO)
    logger.setLevel(level)
    logger.propagate = False

    file_formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)
    console_formatter = logging.Formatter("%(levelname)s - %(message)s")

    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(file_formatter)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(console_formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


logger = setup_logger()
