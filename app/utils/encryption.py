"""Encrypt sensitive values at rest (panel passwords)."""

from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from app.config.settings import settings

logger = logging.getLogger(__name__)
_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet

    raw = settings.SECRET_KEY or settings.BOT_TOKEN
    if not settings.SECRET_KEY:
        logger.warning(
            "SECRET_KEY is not set — deriving encryption key from BOT_TOKEN. "
            "Set SECRET_KEY in .env for production."
        )
    key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
    _fernet = Fernet(key)
    return _fernet


def encrypt(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    return _get_fernet().decrypt(ciphertext.encode()).decode()


def decrypt_if_needed(value: str) -> str:
    """Return plaintext; supports legacy unencrypted values in the database."""
    if not value:
        return ""
    try:
        return decrypt(value)
    except (InvalidToken, ValueError, TypeError):
        return value
