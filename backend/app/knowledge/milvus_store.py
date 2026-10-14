"""Milvus REST v2 adapter. Business authorization and source text stay in SQL."""
import hashlib
import json
import math
import re
import time
from uuid import UUID

import requests

from ..db.models import EMBEDDING_DIMENSION
from .request_budget import active_deadline, remaining_timeout


class VectorStoreUnavailable(RuntimeError):
    pass


def vector_record_id(chunk_id, generation):
    return hashlib.sha256(f"{chunk_id}:{generation}".encode()).hexdigest()


class MilvusVectorStore:
    def __init__(self, settings, *, transport=None):
        self.uri = getattr(settings, "milvus_uri", "http://127.0.0.1:19531").rstrip("/")
        self.collection = getattr(settings, "milvus_collection", "enterprise_chunks")
        self.timeout = getattr(settings, "milvus_timeout_seconds", 10)
        self.token = getattr(settings, "milvus_token", "")
        self._transport = transport or requests

    def _call(self, operation, **values):
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        timeout = (min(3, self.timeout), self.timeout)
        if active_deadline():
            remaining = remaining_timeout(self.timeout)
            timeout = (min(3, remaining / 2), remaining / 2)
        try:
            response = self._transport.post(
                self.uri + "/v2/vectordb/" + operation,
                json={"collectionName": self.collection, **values}, headers=headers,
                timeout=timeout,
            )
            response.raise_for_status()
            result = response.json()
            if result.get("code") != 0:
                raise ValueError("Milvus operation rejected")
            return result.get("data")
        except (requests.RequestException, ValueError, TypeError, AttributeError) as exc:
            # Neither URLs, server error text nor authorization headers enter logs.
            raise VectorStoreUnavailable("Milvus 操作失败：" + operation) from exc

    def ensure_collection(self):
        exists = self._call("collections/has")
        if not isinstance(exists, dict) or not exists.get("has"):
            strings = {"id": 64, "chunk_id": 64, "tenant_id": 36,
                       "knowledge_base_id": 36, "document_id": 36, "generation": 32}
            fields = [{"fieldName": name, "dataType": "VarChar",
                       "isPrimary": name == "id", "elementTypeParams": {"max_length": str(size)}}
                      for name, size in strings.items()]
            fields += [{"fieldName": "created_at", "dataType": "Int64"},
                       {"fieldName": "embedding", "dataType": "FloatVector",
                        "elementTypeParams": {"dim": str(EMBEDDING_DIMENSION)}}]
            self._call("collections/create", schema={"autoId": False, "enableDynamicField": False, "fields": fields},
                       indexParams=[{"fieldName": "embedding", "indexName": "chunks_hnsw",
                                     "metricType": "COSINE", "indexType": "HNSW",
                                     "params": {"M": 16, "efConstruction": 64}}],
                       params={"consistencyLevel": "Strong"})
        self.verify(require_loaded=False)
        self._call("collections/load")
        deadline = time.monotonic() + self.timeout
        while self.verify(require_loaded=False).get("load") != "LoadStateLoaded":
            if time.monotonic() >= deadline:
                raise VectorStoreUnavailable("Milvus 集合加载超时。")
            time.sleep(0.2)

    def verify(self, *, require_loaded=True):
        description = self._call("collections/describe")
        fields = {f.get("name", f.get("fieldName")): f for f in description.get("fields", [])}
        required = {"id", "chunk_id", "tenant_id", "knowledge_base_id", "document_id", "generation", "created_at", "embedding"}
        if not required.issubset(fields):
            raise VectorStoreUnavailable("Milvus 集合结构不兼容。")
        parameters = fields["embedding"].get("params", fields["embedding"].get("elementTypeParams", {}))
        if isinstance(parameters, list):
            parameters = {item["key"]: item["value"] for item in parameters}
        if int(parameters.get("dim", 0)) != EMBEDDING_DIMENSION:
            raise VectorStoreUnavailable("Milvus 向量维度不兼容。")
        if not fields["id"].get("primaryKey", fields["id"].get("isPrimary", False)):
            raise VectorStoreUnavailable("Milvus 主键结构不兼容。")
        if require_loaded and description.get("load") != "LoadStateLoaded":
            raise VectorStoreUnavailable("Milvus 集合尚未加载。")
        indexes = description.get("indexes", [])
        if not any(i.get("metricType") == "COSINE" for i in indexes):
            raise VectorStoreUnavailable("Milvus 距离度量不兼容。")
        return description

    @staticmethod
    def _generation(value):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
            raise ValueError("索引版本格式无效")
        return value

    @staticmethod
    def _vector(values):
        if len(values) != EMBEDDING_DIMENSION or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
            raise ValueError("向量必须包含1024个有限数值")
        return [float(v) for v in values]

    @staticmethod
    def _scope_filter(scope):
        ids = sorted(str(UUID(str(value))) for value in scope.knowledge_base_ids)
        if not ids:
            raise ValueError("不允许无授权范围的向量操作")
        return f'tenant_id == {json.dumps(str(scope.tenant_id))} and knowledge_base_id in {json.dumps(ids)}'

    def upsert(self, records):
        if records and (len({row["chunk_id"] for row in records}) != len(records) or any(
            tuple(row[key] for key in ("tenant_id", "knowledge_base_id", "document_id", "generation")) !=
            tuple(records[0][key] for key in ("tenant_id", "knowledge_base_id", "document_id", "generation"))
            for row in records)):
            raise ValueError("候选向量必须属于同一文档版本且标识唯一")
        for record in records:
            self._generation(record["generation"])
            self._vector(record["embedding"])
            for field in ("tenant_id", "knowledge_base_id", "document_id"):
                UUID(record[field])
            if record["id"] != vector_record_id(record["chunk_id"], record["generation"]):
                raise ValueError("向量标识不匹配")
        for offset in range(0, len(records), 64):
            batch = records[offset:offset + 64]
            result = self._call("entities/upsert", data=batch)
            if int(result.get("upsertCount", -1)) != len(batch):
                raise VectorStoreUnavailable("Milvus 未完整写入候选向量。")
        if records:
            first = records[0]
            expression = ' and '.join(f'{field} == {json.dumps(first[field])}'
                                      for field in ("tenant_id", "knowledge_base_id", "document_id", "generation"))
            if self.count(expression) != len(records):
                raise VectorStoreUnavailable("Milvus 候选向量尚未完整可见。")

    def count(self, expression):
        rows = self._call("entities/query", filter=expression, outputFields=["count(*)"], consistencyLevel="Strong")
        return int(rows[0]["count(*)"]) if rows else 0

    def search(self, *, scope, generations, vector, limit):
        if not scope.knowledge_base_ids or not generations:
            return []
        expression = self._scope_filter(scope) + " and generation in " + json.dumps(sorted({self._generation(g) for g in generations}))
        rows = self._call("entities/search", data=[self._vector(vector)], annsField="embedding", limit=limit,
                          filter=expression, outputFields=["chunk_id", "document_id", "tenant_id", "knowledge_base_id", "generation"],
                          searchParams={"metricType": "COSINE", "params": {"ef": max(64, limit)}}, consistencyLevel="Strong")
        if rows and isinstance(rows[0], list):
            rows = rows[0]
        return rows or []

    def delete(self, *, scope, document_id, generation=None):
        expression = self._scope_filter(scope) + " and document_id == " + json.dumps(str(UUID(str(document_id))))
        if generation is not None:
            expression += " and generation == " + json.dumps(self._generation(generation))
        self._call("entities/delete", filter=expression)

    def old_records(self, *, cutoff, offset=0, limit=1000, exclude_generations=()):
        expression = f"created_at < {int(cutoff)}"
        if exclude_generations:
            expression += " and generation not in " + json.dumps(sorted({self._generation(g) for g in exclude_generations}))
        return self._call("entities/query", filter=expression, offset=offset, limit=limit,
                          outputFields=["id", "tenant_id", "knowledge_base_id", "document_id", "generation"],
                          consistencyLevel="Strong") or []


def make_vector_records(chunks, embeddings):
    now = int(time.time())
    return [dict(id=vector_record_id(chunk.id, chunk.vector_generation), chunk_id=chunk.id,
                 tenant_id=str(chunk.tenant_id), knowledge_base_id=str(chunk.knowledge_base_id),
                 document_id=str(chunk.document_id), generation=chunk.vector_generation,
                 created_at=now, embedding=list(vector)) for chunk, vector in zip(chunks, embeddings, strict=True)]
