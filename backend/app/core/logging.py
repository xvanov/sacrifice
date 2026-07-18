"""Log redaction formatter — strips secrets from application logs.

AC2.1: WHEN application logging emits tokens or keys, THE logging layer
SHALL redact the sensitive values.

Hooks into the standard ``logging`` module via a :class:`RedactingFormatter`
that wraps any other formatter and applies regex-based redaction to the
formatted message string.
"""

from __future__ import annotations

import logging
import re
from typing import Tuple


class RedactingFormatter(logging.Formatter):
    """A logging :class:`~logging.Formatter` that redacts secrets.

    Patterns are applied *after* the wrapped formatter has rendered the
    record, so both plain messages and structured/%-style arguments are
    covered.  Traces (``exc_info``) are also redacted.
    """

    _REDACT_PATTERNS: Tuple[Tuple[str, str], ...] = (
        # Query-param patterns (match BEFORE prefix-only patterns so the
        # entire value is consumed in one pass and doesn't leave fragments
        # that a shorter prefix pattern would double-redact).
        # apiKey=<value> (common in Every.org / YouTube API query strings)
        (r"apiKey=[\w\-\.\+/=]+", "apiKey=[REDACTED]"),
        # key=<value> (common YouTube Data API v3) — use a word boundary
        # so we don't match inside longer param names like "apiKey=".
        (r"\bkey=[\w\-\.\+/=]+", "key=[REDACTED]"),
        # client_secret=<value> (OAuth code exchange)
        (r"client_secret=[\w\-\.\+/=]+", "client_secret=[REDACTED]"),
        # _key= and _secret= generic (Every.org / Pledge.to style), 20+
        # alphanumeric chars after the equals sign.
        (r"(?<=_key=)[\w\-\.\+/=]{20,}", "[REDACTED]"),
        (r"(?<=_secret=)[\w\-\.\+/=]{20,}", "[REDACTED]"),
        # Bearer / token auth headers — "Bearer <token>" → "Bearer [REDACTED]"
        (r"Bearer\s+[\w\-\.\+/=]+", "Bearer [REDACTED]"),
        # Stripe secret keys — sk_live_… / sk_test_…
        (r"sk_live_[\w]+", "sk_live_[REDACTED]"),
        (r"sk_test_[\w]+", "sk_test_[REDACTED]"),
        # Stripe webhook secrets
        (r"whsec_[\w]+", "whsec_[REDACTED]"),
        # JWT tokens — three base64url segments separated by dots,
        # beginning with "eyJ" (the base64url of {"alg":…})
        (r"eyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+", "JWT=[REDACTED]"),
        # Database / broker DSNs with embedded credentials.
        # postgresql://user:pass@host/db, redis://user:pass@host, etc.
        (r"(?:postgresql|mysql|redis|mongodb|amqp)(?:\+[^:]+)?://[^:@\s]+:[^:@\s]+@",
         "[REDACTED_DSN_WITH_CREDS]@"),
        # HTTP(S) URLs with embedded userinfo — https://user:pass@host
        (r"https?://[^:@/]+:[^:@/]+@", "[REDACTED_URL_WITH_CREDS]@"),
    )

    def __init__(self, fmt: str | None = None, *args, **kwargs):
        super().__init__(fmt, *args, **kwargs)

    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        return self._redact(original)

    @classmethod
    def _redact(cls, message: str) -> str:
        for pattern, replacement in cls._REDACT_PATTERNS:
            message = re.sub(pattern, replacement, message)
        return message


def install_redacting_logging() -> None:
    """Replace every handler's formatter on the root logger with a
    :class:`RedactingFormatter` that delegates to the original formatter.

    Call once during application startup so all log output — including
    third-party library logs — is redacted.
    """
    root = logging.getLogger()
    for handler in root.handlers:
        _wrap_handler(handler)
    # Also patch the root's own formatter so loggers that propagate
    # without their own handler (the common case) are covered.
    if root.handlers:
        return
    # If nothing has configured handlers yet (e.g. uvicorn hasn't started),
    # install a minimal handler so logs emitted before uvicorn takes over
    # are still redacted.
    fallback = logging.StreamHandler()
    fallback.setFormatter(RedactingFormatter("%(message)s"))
    root.addHandler(fallback)
    root.setLevel(logging.INFO)


def _wrap_handler(handler: logging.Handler) -> None:
    existing = handler.formatter
    if existing is None:
        handler.setFormatter(RedactingFormatter())
    elif not isinstance(existing, RedactingFormatter):
        handler.setFormatter(RedactingFormatter(fmt=existing._fmt, datefmt=existing.datefmt))