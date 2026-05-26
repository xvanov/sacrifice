from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5433/sacrifice"
    redis_url: str = "redis://localhost:6379/0"

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

    debug: bool = True

    # Chat matching
    chat_match_model_id: str = "DeepSeek-V4-Flash"
    chat_match_confidence_threshold: float = 0.7

    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    # 32-byte url-safe base64 Fernet key for encrypting sensitive tokens
    # (e.g., user-supplied GitHub PATs) at rest. If empty, a key is derived
    # from jwt_secret so dev environments work out of the box.
    token_encryption_key: str = ""

    model_config = {
        "env_file": "../.env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
