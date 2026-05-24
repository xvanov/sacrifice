"""Symmetric encryption helpers for sensitive values stored at rest.

Tokens are prefixed with ``fernet:`` once encrypted so we can distinguish
ciphertext from legacy plaintext rows already sitting in the database
(``decrypt_token`` returns those unchanged).
"""

import base64
import hashlib

from cryptography.fernet import Fernet

from app.config import settings

_PREFIX = "fernet:"
_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    """Lazily construct the module-level Fernet instance.

    Uses ``settings.token_encryption_key`` when set; otherwise derives a key
    deterministically from ``settings.jwt_secret`` so dev environments work
    without configuring a separate variable.
    """
    global _fernet
    if _fernet is not None:
        return _fernet

    key = settings.token_encryption_key
    if key:
        key_bytes = key.encode() if isinstance(key, str) else key
    else:
        digest = hashlib.sha256(settings.jwt_secret.encode()).digest()
        key_bytes = base64.urlsafe_b64encode(digest)

    _fernet = Fernet(key_bytes)
    return _fernet


def encrypt_token(plaintext: str) -> str:
    """Encrypt ``plaintext`` and return a ``fernet:<ciphertext>`` string."""
    token = _get_fernet().encrypt(plaintext.encode()).decode()
    return f"{_PREFIX}{token}"


def decrypt_token(value: str) -> str:
    """Decrypt a value previously produced by :func:`encrypt_token`.

    If ``value`` does not carry the ``fernet:`` prefix it is treated as
    legacy plaintext and returned unchanged for backwards compatibility
    with rows persisted before encryption was introduced.
    """
    if not value or not value.startswith(_PREFIX):
        return value
    ciphertext = value[len(_PREFIX):]
    return _get_fernet().decrypt(ciphertext.encode()).decode()
