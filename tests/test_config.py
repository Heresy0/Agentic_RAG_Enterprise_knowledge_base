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


@pytest.mark.parametrize('name,value', [('RAG_RERANK_TOP_K','0'),('RAG_RERANK_TOP_K','25'),
    ('RAG_RERANK_TOP_K','bad'),('RAG_PARENT_CONTEXT_ENABLED','yes')])
def test_parent_configuration_rejects_invalid_values(application_environment,monkeypatch,name,value):
    monkeypatch.setenv(name,value)
    with pytest.raises(RuntimeError):Settings()


def test_parent_configuration_is_explicit_and_reproducible(application_environment,monkeypatch):
    monkeypatch.setenv('RAG_RERANK_TOP_K','12')
    monkeypatch.setenv('RAG_PARENT_CONTEXT_ENABLED','true')
    settings=Settings()
    assert settings.rag_rerank_top_k==12 and settings.rag_parent_context_enabled


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


@pytest.mark.parametrize('name,value', [
    ('AGENTIC_RAG_MODE','unbounded'), ('AGENT_MAX_DECISIONS','4'), ('AGENT_MAX_SEARCHES','0'),
    ('AGENT_MAX_READS','3'), ('AGENT_MAX_EVIDENCE','1000'), ('AGENT_MAX_PROMPT_BYTES','999999'),
    ('AGENT_MAX_OUTPUT_TOKENS','0'), ('AGENT_MAX_DECISION_TOKENS','1'), ('AGENT_MAX_SECONDS','nan'),
    ('AGENT_DECISION_TIMEOUT_SECONDS','inf'), ('AGENT_MAX_DECISIONS','not-an-int')])
def test_agent_limits_are_validated(application_environment, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match=name):
        Settings()


@pytest.mark.parametrize("name,value", [
    ("RAG_PLANNER_MODE", "always"), ("RAG_REPAIR_ENABLED", "yes"),
    ("RAG_MAX_OPTIONAL_CALLS", "7"), ("RAG_MAX_OPTIONAL_CALLS", "secret-invalid"),
    ("RAG_MAX_OPTIONAL_TOKENS", "0"), ("RAG_MAX_OPTIONAL_SECONDS", "nan"),
    ("RAG_PLANNER_OUTPUT_TOKENS", "1"), ("RAG_PLANNER_TIMEOUT_SECONDS", "inf"),
    ("RAG_REPAIR_OUTPUT_TOKENS", "9000"), ("RAG_REPAIR_PROMPT_BYTES", "999999"),
    ("RAG_REPAIR_TIMEOUT_SECONDS", "secret-invalid"),
])
def test_rag_routing_limits_are_validated(application_environment, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match=name) as error:
        Settings()
    assert "secret-invalid" not in str(error.value)


def test_rag_routing_defaults(application_environment):
    settings = Settings()
    assert settings.rag_planner_mode == "auto" and settings.rag_repair_enabled
    assert settings.rag_max_optional_calls == 4 and settings.rag_max_optional_tokens == 32000
    assert settings.rag_feedback_enabled and settings.rag_max_searches == 3
    assert settings.rag_regeneration_output_tokens == 2048 and settings.rag_regeneration_timeout_seconds == 15


@pytest.mark.parametrize('name,value', [('RAG_FEEDBACK_ENABLED','yes'), ('RAG_MAX_SEARCHES','0'),
    ('RAG_MAX_SEARCHES','5'), ('RAG_REGENERATION_OUTPUT_TOKENS','1'), ('RAG_REGENERATION_TIMEOUT_SECONDS','nan')])
def test_feedback_limits_are_validated(application_environment, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match=name):
        Settings()
