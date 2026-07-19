"""Tests for secret governance: config-layer rejection of hardcoded defaults.

AC1.1: WHEN backend settings are loaded, THE configuration layer SHALL accept
secrets from approved secure sources only.
AC1.2: WHEN backend settings encounter defaults or hardcoded fallback secret
values, THE configuration layer SHALL reject those secret-loading paths.
"""

import os
from unittest.mock import patch

import pytest


class TestSecretGovernance:
    """Test that secret-bearing settings reject hardcoded defaults."""

    def test_jwt_secret_hardcoded_default_is_rejected(self):
        """AC1.2: jwt_secret='change-me-in-production' must be rejected."""
        from app.config import Settings

        # Provide DATABASE_URL so only jwt_secret's default triggers rejection.
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql+asyncpg://u:p@h/db",
            },
            clear=True,
        ):
            with pytest.raises(ValueError, match="jwt_secret"):
                Settings(_env_file=None)

    def test_database_url_hardcoded_default_is_rejected(self):
        """AC1.2: database_url with hardcoded credentials must be rejected."""
        from app.config import Settings

        # Provide JWT_SECRET so only database_url's default triggers rejection.
        with patch.dict(
            os.environ,
            {
                "JWT_SECRET": "test-jwt",
            },
            clear=True,
        ):
            with pytest.raises(ValueError, match="database_url"):
                Settings(_env_file=None)

    def test_secrets_from_env_vars_are_accepted(self):
        """AC1.1: Secrets set via env vars (approved secure source) are accepted."""
        from app.config import Settings

        with patch.dict(
            os.environ,
            {
                "JWT_SECRET": "prod-secret-from-vault",
                "DATABASE_URL": "postgresql+asyncpg://user:pass@prod-db:5432/sacrifice",
            },
            clear=True,
        ):
            s = Settings(_env_file=None)
            assert s.jwt_secret == "prod-secret-from-vault"
            assert s.database_url == "postgresql+asyncpg://user:pass@prod-db:5432/sacrifice"

    def test_optional_secrets_with_empty_default_are_accepted(self):
        """Empty-string defaults for optional integrations are NOT rejected.

        These are the approved "not configured" sentinel, not dangerous
        hardcoded values. The code already checks ``if not settings.xxx``
        before using them.
        """
        from app.config import Settings

        with patch.dict(
            os.environ,
            {
                "JWT_SECRET": "test-jwt",
                "DATABASE_URL": "postgresql+asyncpg://u:p@h/db",
            },
            clear=True,
        ):
            s = Settings(_env_file=None)
            # Optional integration secrets remain empty — no error.
            assert s.stripe_secret_key == ""
            assert s.google_client_secret == ""
            assert s.github_client_secret == ""
            assert s.youtube_api_key == ""
            assert s.azure_foundry_api_key == ""
            assert s.pledge_api_key == ""
            assert s.every_org_api_key == ""
            assert s.every_org_api_secret == ""

    def test_non_secret_settings_unchanged(self):
        """Non-secret config like frontend_url, debug are not affected."""
        from app.config import Settings

        with patch.dict(
            os.environ,
            {
                "JWT_SECRET": "test-jwt",
                "DATABASE_URL": "postgresql+asyncpg://u:p@h/db",
            },
            clear=True,
        ):
            s = Settings(_env_file=None)
            assert s.frontend_url == "http://localhost:8082"
            assert s.debug is True
            assert s.jwt_algorithm == "HS256"
            assert s.jwt_expire_minutes == 60
            assert s.max_upload_size_bytes == 100 * 1024 * 1024

    def test_default_rejection_message_names_the_field(self):
        """The error message must name the rejected field for operator clarity."""
        from app.config import Settings

        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError) as exc_info:
                Settings(_env_file=None)
        msg = str(exc_info.value)
        assert "jwt_secret" in msg or "database_url" in msg
