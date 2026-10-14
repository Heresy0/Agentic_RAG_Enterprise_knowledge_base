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
        "MILVUS_TOKEN": "test_reader:test-placeholder",
        "MILVUS_TOKEN_FILE": "",
        "MILVUS_WORKER_TOKEN_FILE": "",
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


@pytest.mark.parametrize("value", ["0", "-1", "121", "nan", "inf"])
def test_embedding_timeout_is_bounded(application_environment, monkeypatch, value):
    monkeypatch.setenv("EMBEDDING_TIMEOUT_SECONDS", value)
    with pytest.raises(RuntimeError, match="EMBEDDING_TIMEOUT_SECONDS"):
        Settings()


def test_embedding_timeout_defaults_to_thirty_seconds(application_environment, monkeypatch):
    monkeypatch.delenv("EMBEDDING_TIMEOUT_SECONDS", raising=False)
    assert Settings().embedding_timeout_seconds == 30


@pytest.mark.parametrize("name,value", [
    ("MILVUS_URI", "file:///tmp/store"), ("MILVUS_URI", "http://user:secret@host:19530"),
    ("MILVUS_COLLECTION", 'bad"collection'), ("MILVUS_TIMEOUT_SECONDS", "nan"),
    ("MILVUS_TIMEOUT_SECONDS", "0"), ("MILVUS_TIMEOUT_SECONDS", "121"),
])
def test_milvus_configuration_is_validated(application_environment, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match=name):
        Settings()


def test_milvus_credentials_fail_closed(application_environment, monkeypatch, tmp_path):
    monkeypatch.setenv("MILVUS_TOKEN", "")
    with pytest.raises(RuntimeError, match="缺少 Milvus"):
        Settings()
    monkeypatch.setenv("MILVUS_TOKEN_FILE", str(tmp_path / "missing"))
    with pytest.raises(RuntimeError, match="无法读取"):
        Settings()
    path = tmp_path / "credential"
    path.write_text("secret-without-username", encoding="utf-8")
    monkeypatch.setenv("MILVUS_TOKEN_FILE", str(path))
    with pytest.raises(RuntimeError, match="username:password") as error:
        Settings()
    assert "secret-without-username" not in str(error.value)


def test_milvus_secret_file_and_purpose_override(application_environment, monkeypatch, tmp_path):
    reader = tmp_path / "reader"
    writer = tmp_path / "writer"
    reader.write_text("reader:read-secret\n", encoding="utf-8")
    writer.write_text("writer:write-secret\n", encoding="utf-8")
    monkeypatch.setenv("MILVUS_TOKEN_FILE", str(reader))
    monkeypatch.setenv("MILVUS_WORKER_TOKEN_FILE", str(writer))
    assert Settings().milvus_token == "reader:read-secret"
    assert Settings(milvus_access="write").milvus_token == "writer:write-secret"
