"""Local SQL sources for M2 tests; never imported by application code."""
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from backend.app.db.models import DocumentChunk, KnowledgeDocument
from backend.app.manufacturing.contracts import WorkOrderContext, WorkOrderFixtures
from backend.app.manufacturing.metadata import (ManufacturingMetadata, ManufacturingReview, SourceAnchor, digest)
from backend.app.security.business_scope import BusinessScope
from backend.app.security.retrieval_scope import RetrievalScope

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = WorkOrderFixtures.model_validate_json((ROOT / "sample_docs/manufacturing_workorder_v1/work_orders.json").read_text(encoding="utf-8"))


def context(work_order="WO-018", **changes):
    record = next(row for row in FIXTURES.records if row.work_order_id == work_order)
    if changes:
        record = type(record).model_validate({**record.model_dump(), **changes})
    now = datetime(2026, 10, 31, 8, tzinfo=timezone.utc)
    return WorkOrderContext(record=record, fetched_at=now, last_verified_at=now, payload_sha256=record.payload_sha256)


def add_source(session, *, tenant, kb, code, revision="1.0", role="reference", text="经审核的制造原文。",
               work_orders=(), rules=(), published=True, **changes):
    identifier = uuid4()
    generation = uuid4().hex
    chunk = DocumentChunk(id=digest(str(identifier)), tenant_id=tenant, knowledge_base_id=kb,
        document_id=identifier, chunk_index=0, content=text, content_hash=digest(text),
        keyword_text="制造 指导 工单", chunking_version="test-m2", embedding_model="test", vector_generation=generation,
        metadata_json={"section_path": "正文"})
    anchor = SourceAnchor(chunk_id=chunk.id, vector_generation=generation,
        chunk_content_hash=chunk.content_hash, quote=text, quote_sha256=digest(text))
    values = dict(document_family_id="HC-WI-A17-020" if role == "instruction" else code,
        document_code=code, business_revision=revision, source_kind="written_notice" if role == "change_notice" else "formal_document",
        document_role=role, scope_kind="work_order" if work_orders else "product_operation",
        product_models=("HC-A17",), operation_codes=("OP-20",), work_order_ids=work_orders,
        source_anchors=(anchor,), rules=tuple({**rule, "anchor": anchor} for rule in rules))
    data = ManufacturingMetadata(**{**values, **changes})
    source_hash = digest("source:" + text + str(identifier))
    doc = KnowledgeDocument(id=identifier, tenant_id=tenant, knowledge_base_id=kb, source_id=digest(code+str(identifier)),
        file_name=code+"_"+revision+".txt", storage_uri="file:///m2/"+identifier.hex+".txt", mime_type="text/plain",
        content_hash=source_hash, status="ready", version=1, file_size_bytes=len(text.encode()), metadata_json={})
    review = ManufacturingReview(revision=2, state="reviewed", data=data, data_sha256=data.payload_digest,
        document_version=1, document_content_hash=source_hash, reviewed_by=uuid4(),
        reviewed_at=datetime.now(timezone.utc), review_note="逐字核对原文及范围")
    doc.metadata_json = {"chunk_count": 1, "manufacturing": review.model_dump(mode="json"),
        "publication": {"state": "published" if published else "draft", "revision": 1}}
    session.add(doc); session.flush(); session.add(chunk); session.flush()
    return doc, chunk, data


def seed_catalog(session, tenant, kb):
    docs = {}
    def add(key, **kwargs):
        docs[key] = add_source(session, tenant=tenant, kb=kb, **kwargs)
    add("old", code="HC-WI-A17-020", role="instruction", text="1.0：实际开工在切换前的在制工单，连续两件首件合格。",
        effective_from="2026-01-01", effective_to="2026-09-30", archived=True)
    add("new", code="HC-WI-A17-020", revision="2.0", role="instruction", text="2.0：连续三件全部规定项目合格并经质量签字。",
        effective_from="2026-10-01")
    common = dict(target_document_code="HC-WI-A17-020", product_model="HC-A17", operation_code="OP-20")
    add("ecn", code="ECN-2026-018", role="change_notice",
        text="按实际开工日期切换：2026-10-01前开工沿用1.0，2026-10-01及以后开工执行2.0；报工日期不作为依据。",
        effective_from="2026-10-01", rules=(
            dict(common, rule_id="old-window", kind="start_before", target_business_revision="1.0", cutoff_date="2026-10-01"),
            dict(common, rule_id="new-window", kind="start_on_or_after", target_business_revision="2.0", cutoff_date="2026-10-01")))
    for key, wo, rev in (("notice19", "WO-019", "2.0"), ("notice24a", "WO-024", "2.0"), ("notice24b", "WO-024", "1.0")):
        code = {"notice19": "HC-SW-019", "notice24a": "HC-SW-024-A", "notice24b": "HC-SW-024-B"}[key]
        add(key, code=code, role="change_notice", work_orders=(wo,), text=f"{code}：{wo}自2026-10-03按{rev}执行，无其他替代关系。",
            rules=(dict(common, rule_id=key, kind="work_order_override", target_business_revision=rev,
                        work_order_ids=(wo,), effective_from="2026-10-03"),))
    add("qc", code="HC-QC-A17-001", role="inspection", text="外径19.95～20.05 mm与孔径12.00～12.10 mm均含上下限。")
    add("event17", code="EVENT-17", role="event_record", work_orders=("WO-017",), text="WO-017更换夹具前的首件已检；不能证明更换后复检完成。")
    add("event18", code="EVENT-18", role="event_record", work_orders=("WO-018",), text="WO-018于9月28日开工，10月2日报工。")
    session.commit()
    return docs


def reviewed_update(session, doc, data):
    previous = doc.metadata_json["manufacturing"]
    review = ManufacturingReview.model_validate({**previous, "revision": previous["revision"]+1,
        "data": data.model_dump(mode="json"), "data_sha256": data.payload_digest})
    doc.metadata_json = {**doc.metadata_json, "manufacturing": review.model_dump(mode="json")}
    session.commit()


def scopes(tenant, kb, user=None):
    return (RetrievalScope(tenant, kb), BusinessScope(tenant, user or uuid4(), "mock-mes-hc", frozenset({"HC-P01"})))
