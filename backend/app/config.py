from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5433/sacrifice"
    redis_url: str = "redis://localhost:6379/0"

    frontend_url: str = "http://localhost:8082"

    google_client_id: str = ""
    google_client_secret: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""

    youtube_api_key: str = ""

    stripe_secret_key: str = ""
    stripe_publishable_key: str = ""
    stripe_webhook_secret: str = ""

    azure_foundry_endpoint: str = ""
    azure_foundry_api_key: str = ""
    azure_foundry_api_version: str = "2024-05-01-preview"
    azure_foundry_deployment: str = "DeepSeek-V4-Flash"

    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    model_config = {
        "env_file": "../.env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
