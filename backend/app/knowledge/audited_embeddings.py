"""Keep LangChain batching; intercept each SDK response before usage is discarded."""
from contextvars import ContextVar

from langchain_core.embeddings import Embeddings

from ..model_usage import ModelCall
from .request_budget import active_deadline, remaining_timeout

_stage = ContextVar("embedding_usage_stage", default="document_embedding")


class EmbeddingClient:
    def __init__(self, client, model, timeout=30):
        self.client, self.model, self.timeout = client, model, timeout

    def create(self, *args, **kwargs):
        if active_deadline():
            kwargs["timeout"] = remaining_timeout(self.timeout)
        with ModelCall(_stage.get(), self.model) as call:
            response = self.client.create(*args, **kwargs)
            data = response if isinstance(response, dict) else response.model_dump()
            call.observe(data.get("usage"), data.get("request_id"))
            return response


class AuditedEmbeddings(Embeddings):
    def __init__(self, embeddings):
        self.embeddings = embeddings
        embeddings.client = EmbeddingClient(embeddings.client, embeddings.model, getattr(embeddings, "request_timeout", 30) or 30)

    def embed_documents(self, texts):
        token = _stage.set("document_embedding")
        try:
            return self.embeddings.embed_documents(texts)
        finally:
            _stage.reset(token)

    def embed_query(self, text):
        token = _stage.set("query_embedding")
        try:
            return self.embeddings.embed_query(text)
        finally:
            _stage.reset(token)

    # Embeddings' default async methods run these audited sync methods in an executor.
