"""Tests for log redaction: secrets must not leak through application logs.

AC2.1: WHEN application logging emits tokens or keys, THE logging layer
SHALL redact the sensitive values.
AC2.2: WHEN redaction behavior is exercised by automated tests, THE test
suite SHALL assert the redaction behavior.
"""

import logging
import io


class TestLogRedaction:
    """Test that the RedactingFormatter redacts sensitive patterns."""

    @staticmethod
    def _capture_log(logger: logging.Logger, level: int, msg: str, *args, **kwargs) -> str:
        """Emit one log record through *logger* and return the formatted string."""
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        handler.setLevel(logging.DEBUG)
        from app.core.logging import RedactingFormatter

        handler.setFormatter(RedactingFormatter("%(message)s"))
        # Ensure the logger processes messages at this level and does NOT
        # propagate to the root logger (which may have no handler / a higher
        # level, causing the message to be silently dropped).
        old_level = logger.level
        old_propagate = logger.propagate
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        logger.addHandler(handler)
        try:
            logger.log(level, msg, *args, **kwargs)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)
            logger.propagate = old_propagate
        return buf.getvalue().strip()

    # ── Bearer tokens ────────────────────────────────────────────────

    def test_bearer_token_in_message_is_redacted(self):
        logger = logging.getLogger("test_redact_bearer_msg")
        output = self._capture_log(
            logger, logging.INFO,
            "Authorization: Bearer sk-abc123secret456",
        )
        assert "sk-abc123secret456" not in output
        assert "Bearer [REDACTED]" in output

    def test_bearer_token_in_exception_is_redacted(self):
        logger = logging.getLogger("test_redact_bearer_exc")
        try:
            raise ValueError("Auth failed with Bearer tok_deadbeef in request")
        except ValueError:
            output = self._capture_log(
                logger, logging.ERROR,
                "Request error",
                exc_info=True,
            )
        assert "tok_deadbeef" not in output
        assert "Bearer [REDACTED]" in output or "[REDACTED]" in output

    # ── API keys in query params ─────────────────────────────────────

    def test_api_key_query_param_is_redacted(self):
        logger = logging.getLogger("test_redact_query")
        output = self._capture_log(
            logger, logging.WARNING,
            "GET https://api.example.com/v1/search?q=foo&apiKey=secret-key-123&take=10",
        )
        assert "secret-key-123" not in output
        # Should redact the value but preserve the structure
        assert "apiKey=[REDACTED]" in output

    def test_key_param_is_redacted(self):
        logger = logging.getLogger("test_redact_key_param")
        output = self._capture_log(
            logger, logging.WARNING,
            "Calling https://api.example.com/v2?key=my-api-token&part=snippet",
        )
        assert "my-api-token" not in output
        assert "key=[REDACTED]" in output

    # ── Stripe keys ──────────────────────────────────────────────────

    def test_stripe_secret_key_is_redacted(self):
        logger = logging.getLogger("test_redact_stripe")
        output = self._capture_log(
            logger, logging.INFO,
            # fake placeholder — not a real key; underscores break Stripe's
            # base62-only key format so this can't be mistaken for a real key
            "Using Stripe key sk_live_FAKE_NOT_A_REAL_KEY_123",
        )
        # The actual secret value must be gone; the prefix + [REDACTED] marker is fine.
        assert "FAKE_NOT_A_REAL_KEY_123" not in output
        assert "sk_live_[REDACTED]" in output

    def test_stripe_test_key_is_redacted(self):
        logger = logging.getLogger("test_redact_stripe_test")
        output = self._capture_log(
            logger, logging.INFO,
            # fake placeholder — not a real key
            "Stripe test key: sk_test_FAKE_NOT_A_REAL_KEY_456",
        )
        assert "sk_test_" not in output or "sk_test_[REDACTED]" in output

    # ── Structured logging (%-style args) ────────────────────────────

    def test_secret_in_percent_format_args_is_redacted(self):
        logger = logging.getLogger("test_redact_percent_args")
        output = self._capture_log(
            logger, logging.WARNING,
            "API call failed with key=%s for query=%s",
            "sk_live_secret_token_12345",
            "normal-query",
        )
        # The actual secret value must be gone.
        assert "sk_live_secret_token_12345" not in output
        # The key= param value is redacted (either by the key= pattern or
        # by the sk_live_ prefix pattern — both are valid redaction paths).
        assert "[REDACTED]" in output
        assert "normal-query" in output  # non-secret preserved

    def test_bearer_in_percent_format_args_is_redacted(self):
        logger = logging.getLogger("test_redact_bearer_args")
        output = self._capture_log(
            logger, logging.WARNING,
            "Auth header: %s",
            "Bearer github_pat_abc123def456",
        )
        assert "github_pat_abc123def456" not in output
        assert "Bearer [REDACTED]" in output

    # ── Multiple secrets in one message ──────────────────────────────

    def test_multiple_secrets_are_all_redacted(self):
        logger = logging.getLogger("test_redact_multi")
        output = self._capture_log(
            logger, logging.WARNING,
            "key=abc123 secret=sk_live_xyz789 token=Bearer tok_def456",
        )
        assert "abc123" not in output
        assert "sk_live_xyz789" not in output
        assert "tok_def456" not in output
        assert "key=[REDACTED]" in output
        assert "sk_live_[REDACTED]" in output
        assert "Bearer [REDACTED]" in output

    # ── Non-secrets pass through unchanged ───────────────────────────

    def test_ordinary_message_is_not_redacted(self):
        logger = logging.getLogger("test_redact_ordinary")
        output = self._capture_log(
            logger, logging.INFO,
            "Goal abc-123 processed successfully in 2.3s",
        )
        assert "Goal abc-123 processed successfully in 2.3s" in output

    def test_url_without_secrets_is_preserved(self):
        logger = logging.getLogger("test_redact_url_clean")
        output = self._capture_log(
            logger, logging.INFO,
            "Redirecting to http://localhost:8082/callback?state=ok",
        )
        assert "http://localhost:8082/callback?state=ok" in output

    # ── JWT tokens ───────────────────────────────────────────────────

    def test_jwt_token_is_redacted(self):
        logger = logging.getLogger("test_redact_jwt")
        # A realistic-looking JWT (header.payload.signature)
        output = self._capture_log(
            logger, logging.INFO,
            "Token: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9nFQKpQEJgFSc",
        )
        assert "eyJhbGci" not in output
        assert "JWT=[REDACTED]" in output

    # ── DSN / connection strings with credentials ────────────────────

    def test_postgresql_dsn_with_credentials_is_redacted(self):
        logger = logging.getLogger("test_redact_postgres_dsn")
        output = self._capture_log(
            logger, logging.ERROR,
            "DB connection: postgresql://admin:hunter2@prod-db:5432/sacrifice",
        )
        assert "hunter2" not in output
        assert "[REDACTED_DSN_WITH_CREDS]@" in output
        # Non-credential parts are preserved after the @
        assert "prod-db:5432/sacrifice" in output

    def test_redis_dsn_with_credentials_is_redacted(self):
        logger = logging.getLogger("test_redact_redis_dsn")
        output = self._capture_log(
            logger, logging.WARNING,
            "Cache at redis://default:my-secret-password@cache-host:6379/0",
        )
        assert "my-secret-password" not in output
        assert "[REDACTED_DSN_WITH_CREDS]@" in output

    def test_asyncpg_dsn_with_credentials_is_redacted(self):
        logger = logging.getLogger("test_redact_asyncpg_dsn")
        output = self._capture_log(
            logger, logging.ERROR,
            "postgresql+asyncpg://postgres:s3cret@localhost:5433/sacrifice",
        )
        assert "s3cret" not in output
        assert "[REDACTED_DSN_WITH_CREDS]@" in output