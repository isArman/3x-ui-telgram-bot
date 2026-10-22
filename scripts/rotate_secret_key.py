#!/usr/bin/env python3
"""
Re-encrypt the stored 3x-ui panel password for the SECRET_KEY currently in use.

The bot derives its Fernet key from SECRET_KEY (sha256 -> url-safe base64), so
the panel password in the database is only readable with the same SECRET_KEY
that the bot runs with. Changing SECRET_KEY therefore locks the stored password
out until it is re-encrypted.

Usage (inside the container, with the new SECRET_KEY already in the environment):

    OLD_SECRET_KEY=<previous secret> python scripts/rotate_secret_key.py

OLD_SECRET_KEY is optional. When it is omitted the script tries the legacy
derivation from BOT_TOKEN and the raw-key interpretation, so it can recover a
password that was written by an older/incorrect rotation.

The script is idempotent: if the stored password already decrypts with the
current key it changes nothing.
"""

from __future__ import annotations

import base64
import hashlib
import os
import sqlite3
import sys
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

DEFAULT_DB = "/app/data/db.sqlite3"


def derive_fernet(secret: str) -> Fernet:
    """The bot's key derivation: sha256(secret) -> url-safe base64."""
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def candidate_keys() -> list[Fernet]:
    """Every key the stored password might have been written with."""
    keys: list[Fernet] = []

    current = os.environ.get("SECRET_KEY", "").strip()
    if current:
        keys.append(derive_fernet(current))
        try:
            # a raw Fernet key stored verbatim (older rotation attempts)
            keys.append(Fernet(current.encode()))
        except (ValueError, TypeError):
            pass

    old = os.environ.get("OLD_SECRET_KEY", "").strip()
    if old and old != current:
        keys.append(derive_fernet(old))
        try:
            keys.append(Fernet(old.encode()))
        except (ValueError, TypeError):
            pass

    token = os.environ.get("BOT_TOKEN", "").strip()
    if token:
        # legacy behaviour: no SECRET_KEY -> key derived from the bot token
        keys.append(derive_fernet(token))

    return keys


def unwrap(fernet: Fernet, value: str) -> str | None:
    """Decrypt a possibly nested Fernet blob; None when it cannot be read."""
    current = value
    for _ in range(10):
        if not current.startswith("gAAAA"):
            return current
        try:
            current = fernet.decrypt(current.encode()).decode()
        except (InvalidToken, ValueError, TypeError):
            return None
    return current


def main() -> int:
    db_path = Path(os.environ.get("DATABASE_PATH", DEFAULT_DB))
    if not db_path.exists():
        print(f"database not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT panel_password FROM panel_settings WHERE id=1"
    ).fetchone()
    blob = (row[0] if row else "") or ""
    if not blob:
        print("no panel password stored — nothing to rotate")
        return 0

    current = derive_fernet(os.environ["SECRET_KEY"].strip())

    # Fernet blobs are non-deterministic, so "is it already correct?" has to be
    # answered by trying to decrypt it with the current key.
    already = unwrap(current, blob)
    if already:
        print("panel password already encrypted with the current key — no change")
        return 0

    plaintext = None
    for key in candidate_keys():
        plaintext = unwrap(key, blob)
        if plaintext:
            break

    if not plaintext:
        print(
            "could not decrypt the stored panel password with any known key",
            file=sys.stderr,
        )
        return 1

    reencrypted = current.encrypt(plaintext.encode()).decode()
    conn.execute(
        "UPDATE panel_settings SET panel_password=? WHERE id=1", (reencrypted,)
    )
    conn.commit()
    print("panel password re-encrypted for the current SECRET_KEY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
