from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5433/sacrifice"
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

    debug: bool = True

    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 15
    refresh_token_expire_days: int = 30

    # 32-byte url-safe base64 Fernet key for encrypting sensitive tokens
    # (e.g., user-supplied GitHub PATs) at rest. If empty, a key is derived
    # from jwt_secret so dev environments work out of the box.
    token_encryption_key: str = ""

    # Direction / goal-type generation
    directions_path: str = "/var/factory/directions"
    direction_synth_model: str = ""  # LLM model for direction synthesis; empty = use azure_foundry_deployment
    chat_spend_cap_millicents: int = 100_000  # $1.00 daily per-user cap
    sacrifice_force_generate: bool = False  # Test-only: bypass chat matcher → always generation path

    # Chat match service: which model to use for goal-type matching and the
    # confidence threshold above which a match is presented to the user.
    chat_match_model_id: str = "DeepSeek-V4-Flash"
    chat_match_confidence_threshold: float = 0.7

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
