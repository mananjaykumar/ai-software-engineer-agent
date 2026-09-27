from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # 1. Application Settings
    PROJECT_NAME: str = "AI Software Engineer Agent"
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # 2. Database Settings (PostgreSQL + pgvector)
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgrespassword"
    POSTGRES_DB: str = "agent_core"

    # 3. Cache & Queue Settings (Redis)
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379

    # 4. LLM API Key (Protected Secret)
    OPENAI_API_KEY: SecretStr = SecretStr("mock_key_for_dev")

    # 5. GitHub Integration Settings
    GITHUB_WEBHOOK_SECRET: SecretStr = SecretStr("mock_webhook_secret_for_dev")
    GITHUB_APP_ID: str = "mock_app_id"
    GITHUB_PRIVATE_KEY: SecretStr = SecretStr("mock_private_key_for_dev")



    @property
    def async_database_url(self) -> str:
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    @property
    def redis_url(self) -> str:
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/0"

    # 6. Pydantic-Settings Configuration
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=True, extra="ignore"
    )


# 7. Cached Getter
@lru_cache
def get_settings() -> Settings:
    return Settings()
