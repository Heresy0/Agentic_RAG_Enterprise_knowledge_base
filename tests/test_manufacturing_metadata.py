"""Manufacturing review is editor-only, versioned, source-bound and atomic."""
from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from test_document_upload_api import api_context, _set_principal
from test_document_publication import indexed, urls
from backend.app.api import document_review
from backend.app.db.models import AuditEvent, DocumentChunk, KnowledgeDocument, KnowledgeBaseUserGrant
from backend.app.manufacturing.metadata import ManufacturingMetadata, SourceAnchor, digest


def prepared(c):
    identifier = indexed(c)
    with c.session_factory() as session:
        chunk = session.get(DocumentChunk, "c"*64)
        chunk.vector_generation = "a"*32
        chunk.content_hash = digest(chunk.content)
        anchor = SourceAnchor(chunk_id=chunk.id, vector_generation=chunk.vector_generation,
            chunk_content_hash=chunk.content_hash, quote=chunk.content, quote_sha256=digest(chunk.content))
        data = ManufacturingMetadata(document_family_id="calibration", document_code="CAL-01", business_revision="1.0",
            source_kind="formal_document", document_role="instruction", scope_kind="product_operation",
            product_models=("HC-A17",), operation_codes=("OP-20",), source_anchors=(anchor,))
        session.commit()
    return identifier, data.model_dump(mode="json")


def payload(data, *, revision=0, state="draft", version=1):
    return dict(expected_document_version=version, expected_revision=revision, metadata=data,
                state=state, review_note="核对当前原文、业务版本及范围")


def test_draft_then_review_then_publish_and_edit_invalidates_both_reviews(api_context):
    c = api_context; identifier, data = prepared(c); base = urls(c, identifier)
    with TestClient(c.app) as client:
        initial = client.get(base+"/manufacturing-metadata")
        assert initial.status_code == 200 and initial.json()["revision"] == 0
        assert initial.headers["cache-control"] == "no-store"
        anchor_preview = client.get(base+"/review?include_parents=true").json()["items"][0]
        assert anchor_preview["content_hash"] == data["source_anchors"][0]["chunk_content_hash"]
        assert anchor_preview["vector_generation"] == "a"*32 and anchor_preview["parent"] is None
        assert client.put(base+"/manufacturing-metadata", json=payload(data, state="reviewed")).status_code == 409
        draft = client.put(base+"/manufacturing-metadata", json=payload(data)); assert draft.status_code == 200
        publish = dict(expected_version=1, expected_revision=draft.json()["document"]["publication"]["revision"],
                       state="published", review_note="已核对")
        assert client.put(base+"/publication", json=publish).status_code == 409
        assert client.put(base+"/manufacturing-metadata", json=payload(data)).status_code == 409
        modified = {**data, "business_revision": "2.0"}
        assert client.put(base+"/manufacturing-metadata", json=payload(modified, revision=1, state="reviewed")).status_code == 409
        reviewed = client.put(base+"/manufacturing-metadata", json=payload(data, revision=1, state="reviewed"))
        assert reviewed.status_code == 200
        assert reviewed.json()["manufacturing"]["reviewed_by"] == str(c.editor.user_id)
        publish["expected_revision"] = reviewed.json()["document"]["publication"]["revision"]
        assert client.put(base+"/publication", json=publish).status_code == 200
        edited = client.put(base+"/manufacturing-metadata", json=payload(modified, revision=2))
        assert edited.status_code == 200
        assert edited.json()["manufacturing"]["state"] == "draft"
        assert edited.json()["manufacturing"]["reviewed_by"] is None
        assert edited.json()["document"]["publication"]["state"] == "draft"
    with c.session_factory() as session:
        events = session.scalars(select(AuditEvent).where(AuditEvent.action == "document.manufacturing_metadata_changed")).all()
        assert len(events) == 3
        assert events[-1].details_json == {"document_version": 1, "manufacturing_revision": 3, "review_state": "draft",
                                         "manufacturing_digest": ManufacturingMetadata.model_validate(modified).payload_digest}
        assert "核对当前" not in str(events[-1].details_json)


@pytest.mark.parametrize("mutation", ["quote", "hash", "chunk", "generation", "extra", "blank_note"])
def test_invalid_or_cross_source_anchors_cannot_be_saved(api_context, mutation):
    c = api_context; identifier, data = prepared(c); value = payload(deepcopy(data))
    anchor = value["metadata"]["source_anchors"][0]
    if mutation == "quote": anchor.update(quote="原文没有这条规定", quote_sha256=digest("原文没有这条规定"))
    elif mutation == "hash": anchor["chunk_content_hash"] = "b"*64
    elif mutation == "chunk": anchor["chunk_id"] = "b"*64
    elif mutation == "generation": anchor["vector_generation"] = "b"*32
    elif mutation == "extra": value["reviewed_by"] = str(uuid4())
    else: value["review_note"] = "  "
    with TestClient(c.app) as client:
        assert client.put(urls(c, identifier)+"/manufacturing-metadata", json=value).status_code == 422
    with c.session_factory() as session:
        assert "manufacturing" not in session.get(KnowledgeDocument, identifier).metadata_json


def test_viewer_and_revoked_editor_cannot_read_or_write_metadata(api_context):
    c = api_context; identifier, data = prepared(c); base = urls(c, identifier)
    _set_principal(c, c.viewer)
    with TestClient(c.app) as client:
        assert client.get(base+"/manufacturing-metadata").status_code == 403
        assert client.put(base+"/manufacturing-metadata", json=payload(data)).status_code == 403
    _set_principal(c, c.editor)
    with c.session_factory() as session:
        grant = session.scalar(select(KnowledgeBaseUserGrant).where(KnowledgeBaseUserGrant.user_id == c.editor.user_id))
        session.delete(grant); session.commit()
    with TestClient(c.app) as client:
        assert client.get(base+"/manufacturing-metadata").status_code == 403


def test_stale_source_blocks_review_and_publication(api_context):
    c = api_context; identifier, data = prepared(c); base = urls(c, identifier)
    with TestClient(c.app) as client:
        assert client.put(base+"/manufacturing-metadata", json=payload(data)).status_code == 200
        assert client.put(base+"/manufacturing-metadata", json=payload(data, revision=1, state="reviewed", version=2)).status_code == 409
        reviewed = client.put(base+"/manufacturing-metadata", json=payload(data, revision=1, state="reviewed")); assert reviewed.status_code == 200
        with c.session_factory() as session:
            session.get(DocumentChunk, "c"*64).content = "同一标识的正文被改变。"; session.commit()
        pub = dict(expected_version=1, expected_revision=reviewed.json()["document"]["publication"]["revision"],
                   state="published", review_note="复核")
        assert client.put(base+"/publication", json=pub).status_code == 409
        assert client.put(base+"/manufacturing-metadata", json=payload(data, revision=2)).status_code == 422


def test_audit_failure_rolls_back_metadata_and_publication(api_context, monkeypatch):
    c = api_context; identifier, data = prepared(c)
    def fail(*args, **kwargs): raise RuntimeError("audit unavailable")
    monkeypatch.setattr(document_review.AuditService, "add", fail)
    with TestClient(c.app, raise_server_exceptions=False) as client:
        assert client.put(urls(c, identifier)+"/manufacturing-metadata", json=payload(data)).status_code == 500
    with c.session_factory() as session:
        doc = session.get(KnowledgeDocument, identifier)
        assert "manufacturing" not in doc.metadata_json
        assert doc.metadata_json["publication"]["revision"] == 1
