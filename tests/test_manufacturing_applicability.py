"""Rules use trusted work-order dates and literal reviewed SQL sources."""
from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import pytest

from test_document_upload_api import api_context
from manufacturing_m2_helpers import context, reviewed_update, scopes, seed_catalog, add_source
from backend.app.db.models import KnowledgeDocument
from backend.app.evaluation.trace import capture_trace
from backend.app.knowledge.chunk_repository import ChunkRepository
from backend.app.manufacturing.applicability import ApplicabilityResolver
from backend.app.manufacturing.metadata import ManufacturingMetadata
from backend.app.security.business_scope import BusinessAuthorizationDenied


@pytest.fixture
def catalog(api_context):
    c = api_context
    with c.session_factory() as session:
        docs = seed_catalog(session, c.tenant_id, c.knowledge_base_id)
        yield c, session, docs


def resolve(c, session, wo="WO-018", **changes):
    scope, business = scopes(c.tenant_id, c.knowledge_base_id, c.editor.user_id)
    return ApplicabilityResolver().resolve(context=context(wo, tenant_id=c.tenant_id, **changes),
        scope=scope, business_scope=business, session=session)


@pytest.mark.parametrize("wo,state,revision", [("WO-017", "applicable", "2.0"), ("WO-018", "applicable", "1.0"),
    ("WO-019", "applicable", "2.0"), ("WO-020", "needs_context", None), ("WO-021", "no_evidence", None),
    ("WO-023", "applicable", "2.0"), ("WO-024", "conflict", None)])
def test_frozen_work_order_scenarios_without_models(catalog, wo, state, revision):
    c, session, docs = catalog
    with capture_trace() as trace:
        result = resolve(c, session, wo)
    assert result.state == state and result.selected_business_revision == revision
    assert trace.stages["manufacturing_selection"][0]["state"] == state
    if state != "applicable":
        assert not result.scope.manufacturing.document_ids
        assert ChunkRepository(session).list_chunks(scope=result.scope) == []


@pytest.mark.parametrize("started,precision,expected", [("2026-09-30", "date", "1.0"),
    ("2026-10-01", "date", "2.0"), ("2026-09-30T16:00:00+00:00", "datetime", "2.0")])
def test_cutoff_is_inclusive_for_new_version_in_explicit_business_timezone(catalog, started, precision, expected):
    c, session, _ = catalog
    result = resolve(c, session, actual_started_at=started, actual_start_precision=precision, reported_at="2030-01-01")
    assert result.selected_business_revision == expected


def test_archived_old_instruction_and_later_ecn_are_both_mandatory(catalog):
    c, session, docs = catalog; result = resolve(c, session)
    rows = ChunkRepository(session).manufacturing_evidence(scope=result.scope)
    assert docs["old"][0].id in {row.document_id for row in rows}
    assert docs["ecn"][0].id in {row.document_id for row in rows}
    assert docs["new"][0].id not in result.scope.manufacturing.document_ids
    assert docs["event17"][0].id not in result.scope.manufacturing.document_ids
    assert docs["event18"][0].id in result.scope.manufacturing.document_ids


@pytest.mark.parametrize("change", ["withdraw", "draft_review", "stale_version", "bad_anchor"])
def test_missing_deciding_ecn_never_falls_back_to_latest_instruction(catalog, change):
    c, session, docs = catalog; doc, chunk, data = docs["ecn"]
    if change == "withdraw": doc.metadata_json = {**doc.metadata_json, "publication": {"state": "withdrawn", "revision": 2}}
    elif change == "draft_review":
        raw = deepcopy(doc.metadata_json); raw["manufacturing"]["state"] = "draft"; doc.metadata_json = raw
    elif change == "stale_version": doc.version = 2
    else: chunk.content = "原文变更却保留旧哈希。"
    session.commit()
    result = resolve(c, session)
    assert result.state in ("no_evidence", "needs_context") and not result.scope.manufacturing.document_ids


def test_unknown_route_and_missing_referenced_notice_fail_closed(catalog):
    c, session, docs = catalog
    result = resolve(c, session, "WO-019", change_notice_refs=("MISSING-NOTICE",))
    assert result.state == "needs_context" and result.reason == "referenced_notice_unavailable"
    doc, _, data = docs["new"]
    reviewed_update(session, doc, ManufacturingMetadata.model_validate({**data.model_dump(), "route_revisions": ("R2",)}))
    assert resolve(c, session).reason == "missing_route_revision"


def test_only_written_supersession_resolves_override_conflict_and_cycles_do_not(catalog):
    c, session, docs = catalog
    doc, _, data = docs["notice24a"]
    raw = data.model_dump(); raw["rules"][0]["supersedes_rule_ids"] = ("notice24b",)
    reviewed_update(session, doc, ManufacturingMetadata.model_validate(raw))
    assert resolve(c, session, "WO-024").selected_business_revision == "2.0"
    doc, _, data = docs["notice24b"]
    raw = data.model_dump(); raw["rules"][0]["supersedes_rule_ids"] = ("notice24a",)
    reviewed_update(session, doc, ManufacturingMetadata.model_validate(raw))
    result = resolve(c, session, "WO-024")
    assert result.state == "conflict" and result.reason == "supersession_cycle"


def test_future_or_cancelled_notice_does_not_silently_override_baseline(catalog):
    c, session, docs = catalog; doc, _, data = docs["notice19"]
    raw = data.model_dump(); raw["rules"][0]["effective_from"] = "2027-01-01"
    reviewed_update(session, doc, ManufacturingMetadata.model_validate(raw))
    assert resolve(c, session, "WO-019").selected_business_revision == "1.0"
    reviewed_update(session, doc, ManufacturingMetadata.model_validate({**data.model_dump(), "notice_state": "cancelled"}))
    assert resolve(c, session, "WO-019").selected_business_revision == "1.0"
    reviewed_update(session, doc, ManufacturingMetadata.model_validate({**data.model_dump(), "effective_to": "2026-10-10"}))
    assert resolve(c, session, "WO-019").selected_business_revision == "1.0"


def test_overlapping_inspection_revisions_are_not_merged(catalog):
    c, session, _ = catalog
    add_source(session, tenant=c.tenant_id, kb=c.knowledge_base_id, code="HC-QC-A17-001", revision="2.0", role="inspection")
    session.commit()
    result = resolve(c, session)
    assert result.state == "conflict" and result.reason == "overlapping_supporting_documents"


def test_same_revision_duplicate_document_is_conflict_not_filename_tiebreak(catalog):
    c, session, _ = catalog
    add_source(session, tenant=c.tenant_id, kb=c.knowledge_base_id, code="HC-WI-A17-020", role="instruction")
    session.commit()
    assert resolve(c, session).state == "conflict"


def test_rule_budget_is_bounded_before_supersession_graph_traversal(catalog):
    c, session, _ = catalog
    for number in range(4):
        rules = tuple(dict(rule_id=f"extra-{number}-{i}", kind="work_order_override",
            target_document_code="HC-WI-A17-020", target_business_revision="2.0", product_model="HC-A17",
            operation_code="OP-20", work_order_ids=("WO-019",), effective_from="2026-10-03") for i in range(16))
        add_source(session, tenant=c.tenant_id, kb=c.knowledge_base_id, code=f"EXTRA-{number}", role="change_notice",
            work_orders=("WO-019",), rules=rules)
    session.commit()
    result = resolve(c, session, "WO-019")
    assert result.state == "needs_context" and result.reason == "rule_budget_exceeded"


def test_other_tenant_source_or_plant_denied_before_catalog_read(catalog):
    c, session, _ = catalog; scope, business = scopes(c.tenant_id, c.knowledge_base_id)
    for ctx in (context("WO-022", tenant_id=c.tenant_id), context(),
                context(tenant_id=c.tenant_id, source_system_id="other-mes")):
        with pytest.raises(BusinessAuthorizationDenied):
            ApplicabilityResolver().resolve(context=ctx, scope=scope, business_scope=business, session=session)
