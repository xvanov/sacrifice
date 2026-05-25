"""Password hashing helpers.

Wraps :mod:`passlib` (bcrypt) so callers don't import passlib directly —
this lets us swap the underlying algorithm later without touching routes
or services.
"""
from passlib.context import CryptContext

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plaintext: str) -> str:
    """Return a bcrypt hash of ``plaintext``."""
    return _pwd_context.hash(plaintext)


def verify_password(plaintext: str, password_hash: str) -> bool:
    """Return True iff ``plaintext`` matches ``password_hash``."""
    if not password_hash:
        return False
    try:
        return _pwd_context.verify(plaintext, password_hash)
    except ValueError:
        # malformed hash → treat as non-match
        return False
