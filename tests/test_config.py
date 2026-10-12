import pytest

from backend.app.config import Settings


@pytest.fixture
def application_environment(monkeypatch):
    active = {
        "DATABASE_URL": "postgresql+psycopg://test:test@127.0.0.1/test",
        "REDIS_URL": "redis://127.0.0.1:6379/0",
        "OIDC_ISSUER": "https://identity.example.invalid/realms/test",
        "OIDC_AUDIENCE": "test-api",
        "OIDC_JWKS_URL": "https://identity.example.invalid/certs",
        "DASHSCOPE_MODEL": "test-chat",
        "DASHSCOPE_BASE_URL": "https://models.example.invalid/v1",
        "DASHSCOPE_API_KEY": "test-placeholder",
        "DASHSCOPE_EMBEDDING_MODEL": "test-embedding",
        "DASHSCOPE_RERANK_MODEL": "test-rerank",
        "DASHSCOPE_RERANK_URL": "https://models.example.invalid/rerank",
        "RERANK_TIMEOUT_SECONDS": "5",
        "MODEL_USAGE_PRICES_JSON": "{}",
    }
    for name, value in active.items():
        monkeypatch.setenv(name, value)
    for name in ("DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL", "DEEPSEEK_API_KEY", "OSS_REGION", "OSS_ENDPOINT",
                 "OSS_ACCESS_KEY_ID", "OSS_ACCESS_KEY_SECRET", "OSS_BUCKET"):
        monkeypatch.delenv(name, raising=False)


def test_settings_only_require_active_rag_services(application_environment):
    settings = Settings()
    assert settings.chat_model == "test-chat"
    assert settings.embedding_model == "test-embedding"
    assert not hasattr(settings, "summary_model") and not hasattr(settings, "database_path")


def test_active_credentials_are_still_required(application_environment, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY")
    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
        Settings()
