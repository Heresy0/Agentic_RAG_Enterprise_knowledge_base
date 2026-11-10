from copy import deepcopy

import pytest
from pydantic import ValidationError

from backend.app.manufacturing.contracts import (
    ApplicabilityRule, ManufacturingQuestion, WorkOrderContext, WorkOrderFixtures, WorkOrderRecord,
)
from scripts.manufacturing_workorder_data import fixture_definition, rule_definitions


def record():
    return deepcopy(fixture_definition()["records"][0])


@pytest.mark.parametrize("value,precision", [
    ("2026-09-28", "date"), ("2026-10-26T08:10:00+08:00", "datetime"), (None, None),
])
def test_actual_start_preserves_precision_and_unknowns(value, precision):
    raw = record()
    raw.update(actual_started_at=value, actual_start_precision=precision, reported_at="2026-10-02")
    parsed = WorkOrderRecord.model_validate(raw)
    assert parsed.actual_started_at == value
    assert parsed.actual_start_precision == precision


@pytest.mark.parametrize("value,precision", [
    ("2026-09-28", "datetime"), ("2026-10-26T08:10:00+08:00", "date"),
    ("2026-10-26T08:10:00", "datetime"), ("2026-02-30", "date"),
    ("2026-10-26 08:10:00+08:00", "datetime"), (None, "date"), ("2026-09-28", None),
])
def test_invalid_or_ambiguous_source_times_are_rejected(value, precision):
    raw = record()
    raw.update(actual_started_at=value, actual_start_precision=precision)
    with pytest.raises(ValidationError):
        WorkOrderRecord.model_validate(raw)


@pytest.mark.parametrize("changes", [
    {"product_model": 17}, {"plant_code": "../HC-P01"}, {"service_token": "must-not-be-accepted"},
    {"change_notice_refs": ["HC-SW-019", "HC-SW-019"]}, {"source_updated_at": 1234567890},
    {"source_updated_at": "1234567890"}, {"source_updated_at": "2026-10-31T08:00:00"},
    {"reported_at": "2026-02-30"},
])
def test_source_fields_are_typed_bounded_and_whitelisted(changes):
    with pytest.raises(ValidationError):
        WorkOrderRecord.model_validate(record() | changes)


@pytest.mark.parametrize("extra", ["tenant_id", "plant_code", "source_system_id", "actual_started_at", "url"])
def test_question_cannot_override_server_business_facts_or_source(extra):
    with pytest.raises(ValidationError):
        ManufacturingQuestion.model_validate(dict(question="哪版指导书？", work_order_id="WO-017", **{extra: "forged"}))


@pytest.mark.parametrize("question", ["", " \n ", "x" * 1001])
def test_question_rejects_empty_or_unbounded_input(question):
    with pytest.raises(ValidationError):
        ManufacturingQuestion(question=question, work_order_id="WO-017")


def test_observation_hash_binds_the_record_but_not_object_key_order():
    raw = record()
    parsed = WorkOrderRecord.model_validate(raw)
    assert parsed.payload_sha256 == WorkOrderRecord.model_validate(dict(reversed(list(raw.items())))).payload_sha256
    context = dict(record=parsed, fetched_at="2026-10-31T08:00:00+08:00",
        last_verified_at="2026-10-31T08:00:01+08:00", payload_sha256=parsed.payload_sha256)
    assert WorkOrderContext.model_validate(context).record.work_order_id == "WO-017"
    with pytest.raises(ValidationError, match="fingerprint"):
        WorkOrderContext.model_validate(context | {"payload_sha256": "0" * 64})
    with pytest.raises(ValidationError, match="precedes"):
        WorkOrderContext.model_validate(context | {"last_verified_at": "2026-10-31T07:59:59+08:00"})
    changed = WorkOrderRecord.model_validate(raw | {"product_model": "HC-A18"})
    assert changed.payload_sha256 != parsed.payload_sha256


@pytest.mark.parametrize("change", ["duplicate", "tenant", "source"])
def test_fixture_source_and_identity_isolation(change):
    raw = fixture_definition()
    if change == "duplicate": raw["records"].append(raw["records"][0])
    elif change == "tenant": raw["records"][0]["tenant_id"] = "11111111-1111-4111-8111-111111111111"
    else: raw["records"][0]["source_system_id"] = "another-source"
    with pytest.raises(ValidationError):
        WorkOrderFixtures.model_validate(raw)


def test_rule_annotation_requires_a_bound_quote_and_explicit_override_identity():
    raw = rule_definitions()["rules"][2]
    assert ApplicabilityRule.model_validate(raw).work_order_ids == ("WO-019",)
    for changes in ({"source_quote": "fabricated"}, {"work_order_ids": []},
                    {"effective_from": None}, {"review_state": "reviewed"}):
        with pytest.raises(ValidationError):
            ApplicabilityRule.model_validate(raw | changes)
