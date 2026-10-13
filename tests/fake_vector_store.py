"""Explicit in-memory test double; never selected by application code."""
from copy import deepcopy
from math import sqrt


class FakeVectorStore:
    def __init__(self):
        self.records = {}
        self.fail = False

    def upsert(self, records):
        if self.fail:
            from backend.app.knowledge.milvus_store import VectorStoreUnavailable
            raise VectorStoreUnavailable("Milvus unavailable")
        self.records.update({row["id"]: deepcopy(row) for row in records})

    def delete(self, *, scope, document_id, generation=None):
        if self.fail:
            raise RuntimeError("Milvus unavailable")
        self.records = {key: row for key, row in self.records.items() if not (
            scope.contains(row["tenant_id"], row["knowledge_base_id"]) and row["document_id"] == str(document_id)
            and (generation is None or row["generation"] == generation))}

    def search(self, *, scope, generations, vector, limit):
        hits = []
        for row in self.records.values():
            if not scope.contains(row["tenant_id"], row["knowledge_base_id"]) or row["generation"] not in generations:
                continue
            embedding = row["embedding"]
            norm = sqrt(sum(x*x for x in vector) * sum(x*x for x in embedding))
            score = sum(x*y for x, y in zip(vector, embedding, strict=True)) / norm if norm else 0
            hits.append({**row, "distance": score})
        return sorted(hits, key=lambda row: -row["distance"])[:limit]

    def old_records(self, *, cutoff, limit=1000, exclude_generations=()):
        return [row for row in self.records.values() if row["created_at"] < cutoff
                and row["generation"] not in exclude_generations][:limit]

    def count(self, expression):
        import re
        expected = dict(re.findall(r'(\w+) == "([^"]+)"', expression))
        return sum(all(row[key] == value for key, value in expected.items()) for row in self.records.values())
