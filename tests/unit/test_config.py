from pydantic import SecretStr

from agent_core.core.config import Settings, get_settings


def test_settings_load() -> None:
    settings = get_settings()
    assert isinstance(settings, Settings)
    assert settings.PROJECT_NAME == "AI Software Engineer Agent"


def test_async_database_url() -> None:
    settings = get_settings()
    url = settings.async_database_url
    assert url.startswith("postgresql+asyncpg://")
    assert settings.POSTGRES_DB in url
    assert settings.POSTGRES_SERVER in url


def test_redis_url() -> None:
    settings = get_settings()
    url = settings.redis_url
    assert url.startswith("redis://")
    assert str(settings.REDIS_PORT) in url


def test_settings_singleton_caching() -> None:
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2


def test_secret_str_masking() -> None:
    settings = Settings()
    assert isinstance(settings.OPENAI_API_KEY, SecretStr)
    assert "**********" in str(settings.OPENAI_API_KEY)  # 10 asterisks ✅
    assert settings.OPENAI_API_KEY.get_secret_value() != ""
