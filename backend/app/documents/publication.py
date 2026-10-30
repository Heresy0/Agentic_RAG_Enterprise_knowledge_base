"""Explicit source publication, independent of asynchronous indexing status."""
from datetime import datetime, timezone

from sqlalchemy import or_

from ..db.models import KnowledgeDocument


def publication(document):
    value = (document.metadata_json or {}).get("publication")
    if value is None:
        return {"state": "legacy_published", "revision": 0}
    return value if isinstance(value, dict) else {"state": "draft", "revision": 0}


def is_published(document):
    return publication(document).get("state") in ("published", "legacy_published")


def published_predicate():
    metadata = KnowledgeDocument.metadata_json
    return or_(metadata["publication"].as_string().is_(None),
               metadata["publication"]["state"].as_string() == "published")


def pending_publication(previous=None):
    return {"state": "draft", "revision": (previous or {}).get("revision", 0) + 1}


def require_review(document):
    document.metadata_json = {**dict(document.metadata_json or {}),
                             "publication": pending_publication(publication(document))}


def reviewed_publication(document, *, state, actor_id, note, acknowledge_ocr):
    return {"state": state, "revision": publication(document).get("revision", 0) + 1,
            "document_version": document.version, "content_hash": document.content_hash,
            "chunking_version": (document.metadata_json or {}).get("chunking_version"),
            "reviewed_by": str(actor_id), "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "review_note": note, "ocr_limitation_acknowledged": acknowledge_ocr}
