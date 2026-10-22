"""Tests for the SECRET_KEY rotation helper."""

import base64
import hashlib
import os
import sqlite3

import pytest
from cryptography.fernet import Fernet

from scripts.rotate_secret_key import derive_fernet, main, unwrap


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "db.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE panel_settings (id INTEGER PRIMARY KEY, panel_password TEXT)"
    )
    conn.execute(
        "INSERT INTO panel_settings (id, panel_password) VALUES (1, ?)", ("",)
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("DATABASE_PATH", str(path))
    return path


def _set_password(db, blob):
    conn = sqlite3.connect(db)
    conn.execute("UPDATE panel_settings SET panel_password=? WHERE id=1", (blob,))
    conn.commit()
    conn.close()


def _get_password(db):
    conn = sqlite3.connect(db)
    value = conn.execute(
        "SELECT panel_password FROM panel_settings WHERE id=1"
    ).fetchone()[0]
    conn.close()
    return value


def test_derive_matches_app_derivation():
    secret = "some-secret"
    expected = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    fernet = derive_fernet(secret)
    assert fernet.decrypt(Fernet(expected).encrypt(b"x")) == b"x"


def test_unwrap_reads_nested_blobs():
    key = Fernet(Fernet.generate_key())
    inner = key.encrypt(b"password").decode()
    outer = key.encrypt(inner.encode()).decode()
    assert unwrap(key, outer) == "password"


def test_unwrap_returns_none_for_garbage():
    assert unwrap(Fernet(Fernet.generate_key()), "gAAAAgarbage") is None


def test_rotation_reencrypts_for_the_current_secret(db, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "new-secret")
    monkeypatch.setenv("OLD_SECRET_KEY", "old-secret")
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    old = derive_fernet("old-secret")
    _set_password(db, old.encrypt(b"panel-pw").decode())

    assert main() == 0

    blob = _get_password(db)
    assert derive_fernet("new-secret").decrypt(blob.encode()) == b"panel-pw"


def test_rotation_is_idempotent(db, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "new-secret")
    monkeypatch.setenv("OLD_SECRET_KEY", "old-secret")
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    old = derive_fernet("old-secret")
    _set_password(db, old.encrypt(b"panel-pw").decode())
    assert main() == 0
    first = _get_password(db)

    assert main() == 0
    assert _get_password(db) == first


def test_rotation_recovers_from_the_legacy_bot_token_key(db, monkeypatch):
    """No SECRET_KEY used to be set: the key came from the bot token."""
    monkeypatch.setenv("SECRET_KEY", "new-secret")
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    legacy = derive_fernet("123:abc")
    _set_password(db, legacy.encrypt(b"panel-pw").decode())

    assert main() == 0
    blob = _get_password(db)
    assert derive_fernet("new-secret").decrypt(blob.encode()) == b"panel-pw"


def test_rotation_fails_loudly_with_an_unknown_key(db, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "new-secret")
    monkeypatch.setenv("OLD_SECRET_KEY", "other-secret")
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    unknown = Fernet(Fernet.generate_key())
    _set_password(db, unknown.encrypt(b"panel-pw").decode())

    assert main() == 1
