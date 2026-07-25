from pydantic import Field, model_validator
from pydantic_settings import BaseSettings

# ── Secret-bearing fields whose hardcoded defaults MUST be overridden ──────
# Fields with empty-string defaults ("not configured") are fine — the app
# already gates on ``if not settings.<field>`` before using them.  Only
# non-empty hardcoded defaults (the dangerous ones) are rejected.
_SECRET_FIELDS = frozenset(
    {
        "database_url",
        "jwt_secret",
    }
)


class Settings(BaseSettings):
    database_url: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5433/sacrifice"
    )
    redis_url: str = "redis://localhost:6379/0"

    media_dir: str = Field(
        default="/var/sacrifice/media", validation_alias="SACRIFICE_MEDIA_DIR"
    )

    frontend_url: str = "http://localhost:8082"

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/auth/google/callback"

    github_client_id: str = ""
    github_client_secret: str = ""
    github_redirect_uri: str = "http://localhost:8000/auth/github/callback"

    youtube_api_key: str = ""

    stripe_secret_key: str = ""
    stripe_publishable_key: str = ""
    stripe_webhook_secret: str = ""

    # Every.org Charity API (search + prefilled donate links).
    # public key (pk_…) authenticates search; the private key (sk_…) is for
    # privileged endpoints/webhooks and must never reach the client.
    every_org_api_key: str = ""
    every_org_api_secret: str = ""

    # Pledge.to API — supports SERVER-SIDE donation creation, so failed-goal
    # pledges to public charities disburse automatically (no manual click).
    # When set, it becomes the preferred public-charity source in search.
    pledge_api_key: str = ""

    azure_foundry_endpoint: str = ""
    azure_foundry_api_key: str = ""
    azure_foundry_api_version: str = "2024-05-01-preview"
    azure_foundry_deployment: str = "DeepSeek-V4-Flash"

    # Factory directions volume — bind-mounted from host at runtime
    factory_directions_path: str = "/var/factory/directions"

    sacrifice_media_dir: str = Field(
        default="/var/sacrifice/media", alias="SACRIFICE_MEDIA_DIR"
    )

    max_upload_size_bytes: int = 100 * 1024 * 1024  # 100 MB

    # ── Verification dispatch reconciliation ──────────────────────────────
    # A proof whose verification task never reached (or never completed in) the
    # worker would otherwise sit "pending" forever while the deadline sweep
    # charges the pledge — the user submitted valid proof and gets billed as if
    # they hadn't. A beat task re-dispatches such submissions.
    #
    # The staleness window must exceed the worst-case time a legitimately
    # in-flight verification can take, or the reconciler would duplicate a
    # verification that is still running. Worst case today is roughly 4.5 min:
    # 60s timeout per attempt (app/core/verification_guard.py) x 4 attempts
    # (Celery max_retries=3) + 10s retry delays. 15 minutes leaves ample room.
    verification_dispatch_stale_minutes: int = 15
    # Total enqueue attempts per submission, INCLUDING the one made by the
    # submit-proof request itself: 1 original + 3 reconciler retries.
    verification_dispatch_max_attempts: int = 4
    # Rows claimed per beat tick, so one sweep cannot flood the broker.
    verification_dispatch_batch_size: int = 50

    # ── Operator access ───────────────────────────────────────────────────
    # Shared secret for the operator-only routes (``/api/operator/*``), sent in
    # the ``X-Operator-Token`` header. Those routes expose other users' goals,
    # and nothing on the ``users`` table distinguishes a privileged account, so
    # this is the authorization — see app/core/operator_auth.py.
    #
    # Empty (the default) disables the routes entirely: they return 404. So does
    # a token shorter than ``operator_auth.MIN_TOKEN_LENGTH``, so a placeholder
    # cannot accidentally become a live credential. Generate one with
    # ``python -c "import secrets; print(secrets.token_urlsafe(32))"``.
    operator_api_token: str = ""

    debug: bool = True

    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    # 32-byte url-safe base64 Fernet key for encrypting sensitive tokens
    # (e.g., user-supplied GitHub PATs) at rest. If empty, a key is derived
    # from jwt_secret so dev environments work out of the box.
    token_encryption_key: str = ""

    # Direction / goal-type generation
    directions_path: str = "/var/factory/directions"
    direction_synth_model: str = (
        ""  # LLM model for direction synthesis; empty = use azure_foundry_deployment
    )
    chat_spend_cap_millicents: int = 100_000  # $1.00 daily per-user cap
    sacrifice_force_generate: bool = (
        False  # Test-only: bypass chat matcher → always generation path
    )
    sacrifice_demo_generation_states: bool = False  # Demo-only: expose GET /api/demo/generation-states with fixture-backed banner states

    # Chat match service: which model to use for goal-type matching and the
    # confidence threshold above which a match is presented to the user.
    chat_match_model_id: str = "DeepSeek-V4-Flash"
    chat_match_confidence_threshold: float = 0.7

    @model_validator(mode="after")
    def _reject_hardcoded_secret_defaults(self):
        """Reject hardcoded defaults for secret-bearing fields.

        AC1.1 / AC1.2: Secrets must come from env vars, ``.env`` files, or
        explicit constructor kwargs — never from a hardcoded default in
        source code.  The ``__pydantic_fields_set__`` attribute tracks which
        fields were explicitly provided during construction (from *any*
        source: env, .env, or kwargs).  Fields NOT in that set fell back to
        their class-level default and MUST be rejected.
        """
        for field_name in _SECRET_FIELDS:
            if field_name not in self.__pydantic_fields_set__:
                raise ValueError(
                    f"Secret field '{field_name}' is using its hardcoded "
                    f"default value. Set the {field_name.upper()} "
                    f"environment variable or configure it via .env / vault."
                )
        return self

    def azure_foundry_chat_url(self) -> str:
        """Full chat-completions URL for the Azure AI Foundry models endpoint.

        ``azure_foundry_endpoint`` holds the resource base (e.g.
        ``https://<res>.services.ai.azure.com/models``). The inference API
        lives at ``/chat/completions`` and requires the ``api-version`` query
        param — POSTing to the bare base returns 404.
        """
        base = self.azure_foundry_endpoint.rstrip("/")
        if not base.endswith("/chat/completions"):
            base = f"{base}/chat/completions"
        return f"{base}?api-version={self.azure_foundry_api_version}"

    model_config = {
        "env_file": "../.env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
