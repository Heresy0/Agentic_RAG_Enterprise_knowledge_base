"""Relevant catalog changes fence delivery; unrelated edits do not."""
from copy import deepcopy

import pytest

from test_document_upload_api import api_context
from test_manufacturing_applicability import catalog, resolve
from manufacturing_m2_helpers import add_source, reviewed_update
from backend.app.manufacturing.metadata import ManufacturingMetadata
from backend.app.manufacturing.scope import ManufacturingScopeChanged


def recheck(decision, session):
    decision.scope.require_current_manufacturing(session)


def test_unpublished_unrelated_edit_does_not_invalidate_current_work_order(catalog):
    c, session, docs = catalog
    doc, _, data = docs["event17"]
    doc.metadata_json = {**doc.metadata_json, "publication": {"state": "draft", "revision": 1}}
    session.commit()
    decision = resolve(c, session)
    assert decision.state == "applicable"
    metadata = deepcopy(doc.metadata_json)
    metadata["manufacturing"]["revision"] += 1
    doc.metadata_json = metadata
    session.commit()
    recheck(decision, session)
    assert resolve(c, session).scope.manufacturing.catalog_sha256 == decision.scope.manufacturing.catalog_sha256


@pytest.mark.parametrize("changes", [
    dict(product_models=("OTHER-MODEL",)), dict(operation_codes=("OTHER-OP",)),
    dict(plant_codes=("OTHER-PLANT",)), dict(work_order_ids=("WO-017",), scope_kind="work_order"),
    dict(route_revisions=("OTHER-ROUTE",)),
])
def test_reviewed_unrelated_source_edit_is_ignored_but_scope_change_is_detected(catalog, changes):
    c, session, _ = catalog
    # A known route can exclude a different route. Unknown routes are handled
    # separately below and cannot establish irrelevance.
    doc, _, data = add_source(session, tenant=c.tenant_id, kb=c.knowledge_base_id,
        code="UNRELATED", **changes)
    session.commit()
    from backend.app.manufacturing.applicability import ApplicabilityResolver
    from manufacturing_m2_helpers import context, scopes
    scope, business = scopes(c.tenant_id, c.knowledge_base_id)
    source = context(tenant_id=c.tenant_id, route_revision="R1")
    decision = ApplicabilityResolver().resolve(context=source, scope=scope, business_scope=business, session=session)
    assert decision.state == "applicable"
    reviewed_update(session, doc, ManufacturingMetadata.model_validate({**data.model_dump(), "archived": True}))
    recheck(decision, session)
    # The same document becoming applicable must enter the catalog, even if it
    # was absent from the original selected document IDs.
    related = {**data.model_dump(), "product_models": ("HC-A17",), "operation_codes": ("OP-20",),
        "plant_codes": (), "work_order_ids": (), "route_revisions": (), "scope_kind": "product_operation"}
    reviewed_update(session, doc, ManufacturingMetadata.model_validate(related))
    with pytest.raises(ManufacturingScopeChanged):
        recheck(decision, session)


@pytest.mark.parametrize("change", ["new_notice", "publish_notice", "new_instruction", "withdraw", "remove_scope", "invalid_review"])
def test_new_or_changed_relevant_sources_still_stop_delivery(catalog, change):
    c, session, docs = catalog
    if change == "publish_notice":
        doc, _, _ = add_source(session, tenant=c.tenant_id, kb=c.knowledge_base_id,
            code="NEW-NOTICE", role="change_notice", published=False)
        session.commit()
    decision = resolve(c, session)
    assert decision.state == "applicable"
    if change in ("new_notice", "new_instruction"):
        add_source(session, tenant=c.tenant_id, kb=c.knowledge_base_id, code="NEW-SOURCE",
            role="change_notice" if change == "new_notice" else "instruction")
    elif change == "publish_notice":
        doc.metadata_json = {**doc.metadata_json, "publication": {"state": "published", "revision": 2}}
    elif change == "withdraw":
        doc = docs["ecn"][0]
        doc.metadata_json = {**doc.metadata_json, "publication": {"state": "withdrawn", "revision": 2}}
    elif change == "remove_scope":
        doc, _, data = docs["old"]
        reviewed_update(session, doc, ManufacturingMetadata.model_validate({**data.model_dump(), "plant_codes": ("OTHER-PLANT",)}))
    else:
        # Unknown review validity cannot prove a published source unrelated.
        doc = docs["event17"][0]
        metadata = deepcopy(doc.metadata_json)
        metadata["manufacturing"]["state"] = "draft"
        doc.metadata_json = metadata
    session.commit()
    with pytest.raises(ManufacturingScopeChanged):
        recheck(decision, session)


def test_unknown_route_keeps_route_restricted_candidates_and_requires_context(catalog):
    c, session, _ = catalog
    add_source(session, tenant=c.tenant_id, kb=c.knowledge_base_id, code="ROUTE-SPEC", route_revisions=("R2",))
    session.commit()
    decision = resolve(c, session)
    assert decision.state == "needs_context" and "route_revision" in decision.gaps


def test_published_invalid_review_is_not_excluded_using_untrusted_scope(catalog):
    c, session, docs = catalog
    doc = docs["event17"][0]
    metadata = deepcopy(doc.metadata_json)
    metadata["manufacturing"]["data_sha256"] = "0" * 64
    doc.metadata_json = metadata
    session.commit()
    decision = resolve(c, session)
    assert decision.state == "needs_context" and "manufacturing_review" in decision.gaps
