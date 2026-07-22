from app.utils.encryption import decrypt, encrypt


def test_encrypt_decrypt_roundtrip():
    original = "panel-secret-password"
    ciphertext = encrypt(original)
    assert ciphertext != original
    assert decrypt(ciphertext) == original


def test_encrypt_empty_string():
    assert encrypt("") == ""
    assert decrypt("") == ""
