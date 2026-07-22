from app.utils.encryption import decrypt, encrypt, decrypt_if_needed, _looks_encrypted


def test_double_encryption_roundtrip():
    original = "panel-secret-password"
    once = encrypt(original)
    twice = encrypt(once)
    assert decrypt_if_needed(twice) == original


def test_plaintext_legacy():
    assert decrypt_if_needed("plain-password") == "plain-password"


def test_looks_encrypted():
    token = encrypt("x")
    assert _looks_encrypted(token)
    assert not _looks_encrypted("plain")
