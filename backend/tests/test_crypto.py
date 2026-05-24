import base64
import hashlib
import importlib

import pytest
from cryptography.fernet import Fernet

from app.core import crypto as crypto_module


@pytest.fixture(autouse=True)
def _reset_fernet_cache():
    """Each test starts with a fresh module-level Fernet instance."""
    crypto_module._fernet = None
    yield
    crypto_module._fernet = None


def test_encrypt_decrypt_roundtrip():
    plaintext = "ghp_fake_personal_access_token_value"
    encrypted = crypto_module.encrypt_token(plaintext)

    assert encrypted.startswith("fernet:")
    assert plaintext not in encrypted
    assert crypto_module.decrypt_token(encrypted) == plaintext


def test_decrypt_legacy_plaintext_returns_input_unchanged():
    legacy = "ghp_pretend_this_was_stored_before_encryption"
    assert crypto_module.decrypt_token(legacy) == legacy


def test_decrypt_empty_value_returns_input_unchanged():
    assert crypto_module.decrypt_token("") == ""


def test_missing_key_falls_back_to_jwt_secret_derived_key(monkeypatch):
    monkeypatch.setattr(crypto_module.settings, "token_encryption_key", "")
    monkeypatch.setattr(crypto_module.settings, "jwt_secret", "unit-test-secret")

    expected_key = base64.urlsafe_b64encode(
        hashlib.sha256(b"unit-test-secret").digest()
    )
    expected_fernet = Fernet(expected_key)

    encrypted = crypto_module.encrypt_token("hello")
    ciphertext = encrypted[len("fernet:"):]
    assert expected_fernet.decrypt(ciphertext.encode()).decode() == "hello"


def test_explicit_token_encryption_key_is_used(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(crypto_module.settings, "token_encryption_key", key)

    encrypted = crypto_module.encrypt_token("payload")
    ciphertext = encrypted[len("fernet:"):]
    assert Fernet(key.encode()).decrypt(ciphertext.encode()).decode() == "payload"
