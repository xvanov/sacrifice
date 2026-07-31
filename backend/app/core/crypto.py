"""Symmetric encryption helpers for sensitive values stored at rest.

Tokens are prefixed with ``fernet:`` once encrypted so we can distinguish
ciphertext from legacy plaintext rows already sitting in the database
(``decrypt_token`` returns those unchanged).
"""

import base64
import hashlib
import logging

from cryptography.fernet import Fernet

from app.config import settings

logger = logging.getLogger(__name__)

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
    ciphertext = value[len(_PREFIX) :]
    return _get_fernet().decrypt(ciphertext.encode()).decode()


def resolve_submitted_token(proof_data: dict, criteria_data: dict) -> str | None:
    """Return the plaintext GitHub token for a verification, or ``None``.

    Attributes an undecryptable value by WHERE IT CAME FROM, which is a
    charge-integrity control rather than defensive tidying.

    ``proof_data`` is written only by a goal type's ``submit_proof``, so a value
    there that will not decrypt means our key rotated or our storage corrupted —
    genuinely our fault. Raising lets the caller record ``inconclusive``, which
    never charges.

    ``criteria_data`` is different: criteria are settable at goal creation and no
    goal type declares ``additionalProperties: false``, so a user can plant
    ``github_token: "fernet:garbage"`` there. Treating that as our fault handed
    them a permanently uncollectable pledge — an inconclusive outcome is skipped
    by every deadline sweep, so it reads as silent forgiveness. Ignoring the
    unusable value instead means the clone proceeds unauthenticated and a private
    repo then fails honestly as the user's problem, which charges.

    A token that decrypts is used regardless of source; ``proof_data`` wins,
    matching the previous precedence.
    """
    raw_proof = proof_data.get("github_token") if proof_data else None
    if raw_proof:
        # Let a failure propagate: only we write here.
        return decrypt_token(raw_proof)

    raw_criteria = criteria_data.get("github_token") if criteria_data else None
    if not raw_criteria:
        return None
    try:
        return decrypt_token(raw_criteria)
    except Exception:
        logger.warning(
            "Ignoring an undecryptable github_token found in criteria_data; "
            "proceeding without credentials so the outcome stays chargeable."
        )
        return None
