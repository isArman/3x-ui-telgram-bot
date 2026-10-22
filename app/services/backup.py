"""Online backup of the bot's SQLite database.

Uses SQLite's backup API so the copy is consistent even while the bot is
writing, instead of copying the file underneath a live connection.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
from pathlib import Path


def _backup_sync(source: str, target: str) -> int:
    src = sqlite3.connect(source)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return Path(target).stat().st_size


async def create_backup(source: str | None = None) -> tuple[str, int]:
    """
    Write a consistent copy of the database next to the original.

    Returns (path, size_in_bytes).
    """
    source = source or os.environ.get("DATABASE_URL", "").replace(
        "sqlite+aiosqlite:///", ""
    )
    if not source or not Path(source).exists():
        raise FileNotFoundError(f"database not found: {source}")

    target_dir = Path(source).parent
    target_dir.mkdir(parents=True, exist_ok=True)
    handle, target_path = tempfile.mkstemp(
        prefix="bot-backup-", suffix=".sqlite3", dir=str(target_dir)
    )
    os.close(handle)

    size = await asyncio.to_thread(_backup_sync, source, target_path)
    return target_path, size


async def create_backup_bytes(source: str | None = None) -> bytes:
    """Backup the database and return the file contents."""
    path, _ = await create_backup(source)
    try:
        return await asyncio.to_thread(Path(path).read_bytes)
    finally:
        await asyncio.to_thread(lambda: Path(path).unlink(missing_ok=True))
