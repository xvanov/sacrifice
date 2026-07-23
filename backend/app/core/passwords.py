"""Password hashing helpers and policy validation.

Wraps :mod:`passlib` (bcrypt) so callers don't import passlib directly —
this lets us swap the underlying algorithm later without touching routes
or services.
"""

import re

from passlib.context import CryptContext

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Common passwords that should never be accepted even when they meet
# the minimum length requirement.
_COMMON_PASSWORDS: frozenset[str] = frozenset(
    {
        "password",
        "password1",
        "password123",
        "password1234",
        "12345678",
        "123456789",
        "1234567890",
        "qwerty123",
        "qwertyuiop",
        "letmein123",
        "trustno1",
        "sunshine1",
        "iloveyou1",
        "monkey123",
        "dragon123",
        "football1",
        "baseball1",
        "welcome1",
        "admin12345",
        "changeme1",
        "secret123",
    }
)


def validate_password_strength(plaintext: str) -> str | None:
    """Validate password against the shared policy used by registration.

    Returns an error message string if the password is too weak, or
    ``None`` if it passes all checks.

    Policy rules (applied in addition to Pydantic min_length=8):
    * Must not be a common/trivial password.
    * Must not consist entirely of digits.
    * Must contain at least one non-digit character.
    """
    lowered = plaintext.lower()
    if lowered in _COMMON_PASSWORDS:
        return "Password is too common. Choose a stronger password."

    if re.fullmatch(r"[0-9]+", plaintext):
        return "Password must not consist entirely of digits."

    return None


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
