import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"缺少环境变量：{name}")
    return value


def read_milvus_token_file(filename: str) -> str:
    """Read a native Milvus credential without putting its contents in errors."""
    path = Path(filename)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    try:
        token = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        raise RuntimeError("无法读取 MILVUS_TOKEN_FILE 密钥文件") from None
    username, separator, password = token.partition(":")
    if not separator or not username or not password or "\n" in token or "\r" in token:
        raise RuntimeError("MILVUS_TOKEN_FILE 必须包含 username:password")
    return token


class Settings:
    def __init__(self, *, milvus_access="read"):
        self.log_level = (
            os.getenv("LOG_LEVEL", "INFO").strip().upper()
            or "INFO"
        )
        from .model_usage import parse_prices
        self.model_usage_prices = parse_prices(os.getenv("MODEL_USAGE_PRICES_JSON", "{}"))
        self.agentic_rag_mode = os.getenv("AGENTIC_RAG_MODE", "auto").strip().lower()
        if self.agentic_rag_mode not in {"off", "auto", "always"}:
            raise RuntimeError("AGENTIC_RAG_MODE 必须为 off、auto 或 always")
        agent_limits = {
            "agent_max_decisions": ("AGENT_MAX_DECISIONS", 2, 1, 3),
            "agent_max_searches": ("AGENT_MAX_SEARCHES", 2, 1, 3),
            "agent_max_reads": ("AGENT_MAX_READS", 1, 0, 2),
            "agent_max_evidence": ("AGENT_MAX_EVIDENCE", 24, 8, 32),
            "agent_max_prompt_bytes": ("AGENT_MAX_PROMPT_BYTES", 12000, 2000, 24000),
            "agent_max_output_tokens": ("AGENT_MAX_OUTPUT_TOKENS", 384, 128, 1024),
            "agent_max_decision_tokens": ("AGENT_MAX_DECISION_TOKENS", 16000, 4000, 64000),
        }
        for attribute, (variable, default, minimum, maximum) in agent_limits.items():
            try:
                value = int(os.getenv(variable, str(default)))
            except ValueError:
                raise RuntimeError(f"{variable} 必须为整数") from None
            if not minimum <= value <= maximum:
                raise RuntimeError(f"{variable} 必须在 {minimum} 至 {maximum} 之间")
            setattr(self, attribute, value)
        from math import isfinite
        self.agent_max_seconds = float(os.getenv("AGENT_MAX_SECONDS", "20"))
        self.agent_decision_timeout_seconds = float(os.getenv("AGENT_DECISION_TIMEOUT_SECONDS", "8"))
        for name, value in (("AGENT_MAX_SECONDS", self.agent_max_seconds),
                            ("AGENT_DECISION_TIMEOUT_SECONDS", self.agent_decision_timeout_seconds)):
            if not isfinite(value) or not 0 < value <= 60:
                raise RuntimeError(f"{name} 必须大于 0 且不超过 60 秒")
        self.rag_planner_mode = os.getenv("RAG_PLANNER_MODE", "auto").strip().lower()
        if self.rag_planner_mode not in {"off", "auto"}:
            raise RuntimeError("RAG_PLANNER_MODE 必须为 off 或 auto")
        repair_enabled = os.getenv("RAG_REPAIR_ENABLED", "true").strip().lower()
        if repair_enabled not in {"true", "false"}:
            raise RuntimeError("RAG_REPAIR_ENABLED 必须为 true 或 false")
        self.rag_repair_enabled = repair_enabled == "true"
        feedback_enabled = os.getenv("RAG_FEEDBACK_ENABLED", "true").strip().lower()
        if feedback_enabled not in {"true", "false"}:
            raise RuntimeError("RAG_FEEDBACK_ENABLED 必须为 true 或 false")
        self.rag_feedback_enabled = feedback_enabled == "true"
        parent_enabled = os.getenv("RAG_PARENT_CONTEXT_ENABLED", "true").strip().lower()
        if parent_enabled not in {"true", "false"}:
            raise RuntimeError("RAG_PARENT_CONTEXT_ENABLED 必须为 true 或 false")
        self.rag_parent_context_enabled = parent_enabled == "true"
        try:
            self.rag_rerank_top_k = int(os.getenv("RAG_RERANK_TOP_K", "12"))
        except ValueError:
            raise RuntimeError("RAG_RERANK_TOP_K 必须为整数") from None
        if not 1 <= self.rag_rerank_top_k <= 24:
            raise RuntimeError("RAG_RERANK_TOP_K 必须在1至24之间")
        rag_limits = {
            "rag_max_optional_calls": ("RAG_MAX_OPTIONAL_CALLS", 4, 1, 6),
            "rag_max_optional_tokens": ("RAG_MAX_OPTIONAL_TOKENS", 32000, 4000, 128000),
            "rag_planner_output_tokens": ("RAG_PLANNER_OUTPUT_TOKENS", 768, 256, 2048),
            "rag_repair_output_tokens": ("RAG_REPAIR_OUTPUT_TOKENS", 1536, 512, 4096),
            "rag_repair_prompt_bytes": ("RAG_REPAIR_PROMPT_BYTES", 24000, 4000, 48000),
            "rag_max_searches": ("RAG_MAX_SEARCHES", 3, 1, 4),
            "rag_regeneration_output_tokens": ("RAG_REGENERATION_OUTPUT_TOKENS", 2048, 512, 4096),
        }
        for attribute, (variable, default, minimum, maximum) in rag_limits.items():
            try:
                value = int(os.getenv(variable, str(default)))
            except ValueError:
                raise RuntimeError(f"{variable} 必须为整数") from None
            if not minimum <= value <= maximum:
                raise RuntimeError(f"{variable} 必须在 {minimum} 至 {maximum} 之间")
            setattr(self, attribute, value)
        for attribute, variable, default in (
            ("rag_max_optional_seconds", "RAG_MAX_OPTIONAL_SECONDS", 35),
            ("rag_planner_timeout_seconds", "RAG_PLANNER_TIMEOUT_SECONDS", 8),
            ("rag_repair_timeout_seconds", "RAG_REPAIR_TIMEOUT_SECONDS", 8),
            ("rag_regeneration_timeout_seconds", "RAG_REGENERATION_TIMEOUT_SECONDS", 15),
        ):
            try:
                value = float(os.getenv(variable, str(default)))
            except ValueError:
                raise RuntimeError(f"{variable} 必须为有限数字") from None
            if not isfinite(value) or not 0 < value <= 60:
                raise RuntimeError(f"{variable} 必须大于 0 且不超过 60 秒")
            setattr(self, attribute, value)
        self.database_url = required_env("DATABASE_URL")
        from math import isfinite
        import re
        from urllib.parse import urlparse
        self.milvus_uri = os.getenv("MILVUS_URI", "http://127.0.0.1:19531").strip().rstrip("/")
        self.milvus_collection = os.getenv("MILVUS_COLLECTION", "enterprise_chunks").strip()
        if milvus_access not in ("read", "write"):
            raise ValueError("无效的 Milvus 访问用途")
        token_file = os.getenv("MILVUS_TOKEN_FILE", "").strip()
        if milvus_access == "write":
            token_file = os.getenv("MILVUS_WORKER_TOKEN_FILE", "").strip() or token_file
        self.milvus_token = read_milvus_token_file(token_file) if token_file else os.getenv("MILVUS_TOKEN", "").strip()
        if not self.milvus_token:
            raise RuntimeError("缺少 Milvus 服务凭据：MILVUS_TOKEN_FILE 或 MILVUS_TOKEN")
        self.milvus_timeout_seconds = float(os.getenv("MILVUS_TIMEOUT_SECONDS", "10"))
        uri = urlparse(self.milvus_uri)
        if uri.scheme not in ("http", "https") or not uri.hostname or uri.username or uri.query or uri.fragment:
            raise RuntimeError("MILVUS_URI 必须是有效的 HTTP(S) 服务地址")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,254}", self.milvus_collection):
            raise RuntimeError("MILVUS_COLLECTION 必须是有效的集合名")
        if not isfinite(self.milvus_timeout_seconds) or not 0 < self.milvus_timeout_seconds <= 120:
            raise RuntimeError("MILVUS_TIMEOUT_SECONDS 必须大于0且不超过120秒")

        self.redis_url = required_env("REDIS_URL")
        self.redis_connect_timeout_seconds = float(
            os.getenv(
                "REDIS_CONNECT_TIMEOUT_SECONDS",
                "2",
            )
        )
        self.redis_socket_timeout_seconds = float(
            os.getenv(
                "REDIS_SOCKET_TIMEOUT_SECONDS",
                "2",
            )
        )

        self.qa_rate_limit_requests = int(
            os.getenv(
                "QA_RATE_LIMIT_REQUESTS",
                "60",
            )
        )
        self.qa_rate_limit_window_seconds = int(
            os.getenv(
                "QA_RATE_LIMIT_WINDOW_SECONDS",
                "60",
            )
        )

        self.qa_max_concurrent_requests_per_tenant = int(
            os.getenv(
                "QA_MAX_CONCURRENT_REQUESTS_PER_TENANT",
                "3",
            )
        )
        self.qa_concurrency_lease_seconds = int(
            os.getenv(
                "QA_CONCURRENCY_LEASE_SECONDS",
                "180",
            )
        )

        if (
            self.qa_max_concurrent_requests_per_tenant
            < 1
        ):
            raise RuntimeError(
                "QA_MAX_CONCURRENT_REQUESTS_PER_TENANT "
                "必须是正整数"
            )

        if self.qa_concurrency_lease_seconds < 1:
            raise RuntimeError(
                "QA_CONCURRENCY_LEASE_SECONDS "
                "必须是正整数"
            )

        if self.qa_rate_limit_requests < 1:
            raise RuntimeError(
                "QA_RATE_LIMIT_REQUESTS 必须是正整数"
            )

        if self.qa_rate_limit_window_seconds < 1:
            raise RuntimeError(
                "QA_RATE_LIMIT_WINDOW_SECONDS 必须是正整数"
            )

        if self.redis_connect_timeout_seconds <= 0:
            raise RuntimeError(
                "REDIS_CONNECT_TIMEOUT_SECONDS 必须大于 0"
            )

        if self.redis_socket_timeout_seconds <= 0:
            raise RuntimeError(
                "REDIS_SOCKET_TIMEOUT_SECONDS 必须大于 0"
            )

        self.oidc_issuer = required_env("OIDC_ISSUER").rstrip("/")
        self.oidc_audience = required_env("OIDC_AUDIENCE")
        self.oidc_jwks_url = required_env("OIDC_JWKS_URL")
        self.oidc_frontend_client_id = (
            os.getenv("OIDC_FRONTEND_CLIENT_ID", "enterprise-knowledge-web").strip()
            or "enterprise-knowledge-web"
        )

        self.chat_model = required_env("DASHSCOPE_MODEL")
        self.chat_base_url = required_env("DASHSCOPE_BASE_URL")
        self.chat_api_key = required_env("DASHSCOPE_API_KEY")

        self.embedding_model = required_env("DASHSCOPE_EMBEDDING_MODEL")
        self.embedding_timeout_seconds = float(os.getenv("EMBEDDING_TIMEOUT_SECONDS", "30"))
        if not 0 < self.embedding_timeout_seconds <= 120:
            raise RuntimeError("EMBEDDING_TIMEOUT_SECONDS 必须大于0且不超过120秒")

        self.rerank_model = required_env("DASHSCOPE_RERANK_MODEL")
        self.rerank_url = required_env("DASHSCOPE_RERANK_URL")
        self.rerank_timeout_seconds = float(required_env("RERANK_TIMEOUT_SECONDS"))

        self.document_storage_dir = Path(
            os.getenv(
                "DOCUMENT_STORAGE_DIR",
                str(PROJECT_ROOT / "data" / "documents"),
            )
        ).resolve()

        self.max_upload_bytes = int(
            os.getenv(
                "MAX_UPLOAD_BYTES",
                str(20 * 1024 * 1024),
            )
        )

        if self.max_upload_bytes < 1:
            raise RuntimeError(
                "MAX_UPLOAD_BYTES 必须是正整数"
            )

        self.indexing_worker_poll_seconds = float(
            os.getenv(
                "INDEXING_WORKER_POLL_SECONDS",
                "2",
            )
        )
        self.indexing_job_retry_delay_seconds = float(
            os.getenv(
                "INDEXING_JOB_RETRY_DELAY_SECONDS",
                "30",
            )
        )
        self.indexing_job_stale_after_seconds = float(
            os.getenv(
                "INDEXING_JOB_STALE_AFTER_SECONDS",
                "300",
            )
        )
        self.indexing_failed_file_retention_hours = float(
            os.getenv(
                "INDEXING_FAILED_FILE_RETENTION_HOURS",
                "24",
            )
        )
        self.indexing_worker_heartbeat_seconds = float(
            os.getenv(
                "INDEXING_WORKER_HEARTBEAT_SECONDS",
                "10",
            )
        )
        self.indexing_orphan_file_retention_hours = float(os.getenv("INDEXING_ORPHAN_FILE_RETENTION_HOURS", "24"))
        if not 0 < self.indexing_orphan_file_retention_hours <= 8760:
            raise RuntimeError("INDEXING_ORPHAN_FILE_RETENTION_HOURS 必须介于 0 和 8760 小时之间")
        self.indexing_worker_stale_seconds = float(
            os.getenv(
                "INDEXING_WORKER_STALE_SECONDS",
                "30",
            )
        )
        self.indexing_worker_metrics_port = int(
            os.getenv(
                "INDEXING_WORKER_METRICS_PORT",
                "9101",
            )
        )

        if self.indexing_worker_poll_seconds <= 0:
            raise RuntimeError(
                "INDEXING_WORKER_POLL_SECONDS 必须大于 0"
            )

        if self.indexing_job_retry_delay_seconds < 0:
            raise RuntimeError(
                "INDEXING_JOB_RETRY_DELAY_SECONDS 不能小于 0"
            )

        if self.indexing_job_stale_after_seconds <= 0:
            raise RuntimeError(
                "INDEXING_JOB_STALE_AFTER_SECONDS 必须大于 0"
            )

        if self.indexing_failed_file_retention_hours <= 0:
            raise RuntimeError(
                "INDEXING_FAILED_FILE_RETENTION_HOURS "
                "必须大于 0"
            )

        if self.indexing_worker_heartbeat_seconds <= 0:
            raise RuntimeError(
                "INDEXING_WORKER_HEARTBEAT_SECONDS 必须大于 0"
            )

        if (
            self.indexing_worker_stale_seconds
            < self.indexing_worker_heartbeat_seconds * 2
        ):
            raise RuntimeError(
                "INDEXING_WORKER_STALE_SECONDS 至少应为心跳"
                "间隔的两倍"
            )

        if not 1 <= self.indexing_worker_metrics_port <= 65535:
            raise RuntimeError(
                "INDEXING_WORKER_METRICS_PORT 必须是有效端口"
            )
