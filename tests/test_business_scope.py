from uuid import uuid4
import json

import pytest
from sqlalchemy import create_engine, delete, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app.audit_service import AuditService
from backend.app.db.models import (Tenant, User, KnowledgeBase, KnowledgeBaseUserGrant, AuditEvent,
                                   Department, DepartmentMembership, KnowledgeBaseDepartmentGrant)
from backend.app.db.business_models import BusinessScopeGrant, ConversationBusinessBinding, QARunBusinessScope
from backend.app.db.conversation_models import Conversation
from backend.app.db.quality_models import QARun
from backend.app.manufacturing.grants import BusinessGrantConfig, apply_grants, preview_grants
from backend.app.security.authorization import AuthorizationService
from backend.app.security.business_scope import BusinessAuthorizationDenied, BusinessAuthorizationService, BusinessScope
from backend.app.security.principal import Principal
from scripts import provision_business_access


@pytest.fixture
def data():
    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool)
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    for model in (Tenant, User, Department, DepartmentMembership, KnowledgeBase,
                  KnowledgeBaseUserGrant, KnowledgeBaseDepartmentGrant, Conversation, QARun,
                  BusinessScopeGrant, ConversationBusinessBinding, QARunBusinessScope, AuditEvent):
        model.__table__.create(engine)
    tenant, other, user_id, foreign_id, kb = [uuid4() for _ in range(5)]
    with Session(engine) as session:
        session.add_all([Tenant(id=tenant, name="M1 test"), Tenant(id=other, name="M1 other")]); session.flush()
        session.add_all([User(id=user_id, tenant_id=tenant, external_subject="m1-owner", name="owner"),
            User(id=foreign_id, tenant_id=other, external_subject="m1-foreign", name="foreign"),
            KnowledgeBase(id=kb, tenant_id=tenant, name="M1 private", visibility="restricted")]); session.flush()
        session.add(KnowledgeBaseUserGrant(tenant_id=tenant, user_id=user_id, knowledge_base_id=kb, permission="admin"))
        session.commit()
        yield session, Principal(user_id, tenant, "m1-owner"), other, foreign_id, kb
    engine.dispose()


def grant(session, principal, permission="view", plant="HC-P01"):
    session.add(BusinessScopeGrant(tenant_id=principal.tenant_id, user_id=principal.user_id,
        source_system_id="mock-mes-hc", plant_code=plant, permission=permission))
    session.commit()


def config(principal, permissions=("view", "review")):
    return BusinessGrantConfig.model_validate(dict(schema_version="business-grants-v1",
        tenant_id=str(principal.tenant_id), source_system_id="mock-mes-hc", grants=[dict(
            user_id=str(principal.user_id), external_subject=principal.external_subject,
            plant_code="HC-P01", permissions=list(permissions))]))


def test_knowledge_admin_has_no_implicit_business_access(data):
    session, principal, _, _, kb = data
    assert AuthorizationService(session).get_permission(principal=principal, knowledge_base_id=kb) == "admin"
    with pytest.raises(BusinessAuthorizationDenied):
        BusinessAuthorizationService(session).require_scope(principal=principal, source_system_id="mock-mes-hc")


def test_view_and_review_are_explicit_and_source_scoped(data):
    session, principal, other, _, _ = data
    grant(session, principal, "review")
    auth = BusinessAuthorizationService(session)
    for permission in ("view", "review"):
        with pytest.raises(BusinessAuthorizationDenied):
            auth.require_scope(principal=principal, source_system_id="mock-mes-hc", permission=permission)
    grant(session, principal)
    scope = auth.require_scope(principal=principal, source_system_id="mock-mes-hc", permission="review")
    assert scope.contains(principal.tenant_id, "mock-mes-hc", "HC-P01")
    assert not scope.contains(other, "mock-mes-hc", "HC-P01")
    assert not scope.contains(principal.tenant_id, "other-source", "HC-P01")
    assert not scope.contains(principal.tenant_id, "mock-mes-hc", "HC-P02")
    with pytest.raises(BusinessAuthorizationDenied):
        auth.require_scope(principal=principal, source_system_id="other-source")


@pytest.mark.parametrize("change", ["subject", "user", "tenant"])
def test_inactive_or_rebound_principal_is_denied(data, change):
    session, principal, *_ = data
    grant(session, principal)
    if change == "subject": session.get(User, principal.user_id).external_subject = "rebound"
    elif change == "user": session.get(User, principal.user_id).status = "disabled"
    else: session.get(Tenant, principal.tenant_id).status = "disabled"
    session.commit()
    with pytest.raises(BusinessAuthorizationDenied):
        BusinessAuthorizationService(session).require_scope(principal=principal, source_system_id="mock-mes-hc")


def test_scope_is_immutable_and_empty_scope_cannot_contain_data():
    tenant, user = uuid4(), uuid4()
    plants = {"HC-P01"}
    scope = BusinessScope(tenant, user, "mock-mes-hc", plants)
    plants.add("HC-P02")
    assert scope.plant_codes == {"HC-P01"}
    assert not BusinessScope(tenant, user, "mock-mes-hc", frozenset()).contains(tenant, "mock-mes-hc", "HC-P01")
    assert not scope.contains("not-a-uuid", "mock-mes-hc", "HC-P01")


def test_revocation_does_not_delete_historical_access_restrictions(data):
    session, principal, *_ = data
    grant(session, principal)
    auth = BusinessAuthorizationService(session)
    scope = auth.require_scope(principal=principal, source_system_id="mock-mes-hc")
    run_id = uuid4()
    session.add(QARun(id=run_id, tenant_id=principal.tenant_id, user_id=principal.user_id,
        request_id=uuid4().hex, question="private", retrieval_question="private", response_json={},
        snapshot_json={}, snapshot_status="captured", config_json={})); session.flush()
    session.add(QARunBusinessScope(tenant_id=principal.tenant_id, run_id=run_id,
        source_system_id="mock-mes-hc", plant_code="HC-P01", work_order_id="WO-017")); session.commit()
    session.execute(delete(BusinessScopeGrant)); session.commit()
    with pytest.raises(BusinessAuthorizationDenied): auth.require_current(principal=principal, scope=scope)
    assert session.scalar(select(func.count()).select_from(QARunBusinessScope)) == 1


@pytest.mark.parametrize("change", ["tenant", "permission", "blank", "duplicate"])
def test_grant_database_constraints(data, change):
    session, principal, other, foreign_user, _ = data
    values = dict(tenant_id=principal.tenant_id, user_id=principal.user_id,
        source_system_id="mock-mes-hc", plant_code="HC-P01", permission="view")
    if change == "tenant": values["user_id"] = foreign_user
    elif change == "permission": values["permission"] = "admin"
    elif change == "blank": values["plant_code"] = " "
    else: grant(session, principal)
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(BusinessScopeGrant(**values)); session.flush()


def test_provision_is_idempotent_audited_and_view_revocation_removes_review(data):
    session, principal, *_ = data
    inputs = config(principal)
    with session.begin(): assert apply_grants(session, inputs)["changed"] == 2
    with session.begin(): assert apply_grants(session, inputs)["changed"] == 0
    with session.begin(): assert apply_grants(session, config(principal, ("view",)), operation="revoke")["changed"] == 2
    assert session.scalar(select(func.count()).select_from(BusinessScopeGrant)) == 0
    assert session.scalar(select(func.count()).select_from(AuditEvent)) == 3


def test_provision_preflights_every_subject_without_creating_or_rebinding_users(data):
    session, principal, *_ = data
    raw = config(principal).model_dump(mode="json")
    raw["grants"].append(dict(user_id=str(uuid4()), external_subject="unknown", plant_code="HC-P01", permissions=["view"]))
    with pytest.raises(ValueError, match="sub"), session.begin():
        apply_grants(session, BusinessGrantConfig.model_validate(raw))
    assert session.scalar(select(func.count()).select_from(BusinessScopeGrant)) == 0
    assert session.scalar(select(func.count()).select_from(AuditEvent)) == 0


def test_audit_failure_rolls_back_permission_changes(data, monkeypatch):
    session, principal, *_ = data
    def failed(*args, **kwargs): raise RuntimeError("audit unavailable")
    monkeypatch.setattr(AuditService, "add", failed)
    with pytest.raises(RuntimeError), session.begin(): apply_grants(session, config(principal))
    assert session.scalar(select(func.count()).select_from(BusinessScopeGrant)) == 0


def test_cli_preview_never_connects_to_a_database(data, tmp_path, monkeypatch, capsys):
    _, principal, *_ = data
    path = tmp_path / "grants.json"; path.write_text(config(principal).model_dump_json(), encoding="utf-8")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    def unexpected_connect(*args, **kwargs): pytest.fail("preview accessed a database")
    monkeypatch.setattr("backend.app.db.session.create_database_engine", unexpected_connect)
    result = provision_business_access.main(["--config", str(path)])
    assert result["apply"] is False and result["database_accessed"] is False
    assert json.loads(capsys.readouterr().out)["requested_permissions"] == 2


def test_review_config_requires_an_explicit_view_permission(data):
    _, principal, *_ = data
    with pytest.raises(ValueError): preview_grants(config(principal, ("review",)))


def test_review_can_be_revoked_without_removing_view(data):
    session, principal, *_ = data
    with session.begin(): apply_grants(session, config(principal))
    with session.begin():
        result = apply_grants(session, config(principal, ("review",)), operation="revoke")
    assert result["changed"] == 1
    auth = BusinessAuthorizationService(session)
    assert auth.require_scope(principal=principal, source_system_id="mock-mes-hc").plant_codes == {"HC-P01"}
    with pytest.raises(BusinessAuthorizationDenied):
        auth.require_scope(principal=principal, source_system_id="mock-mes-hc", permission="review")
