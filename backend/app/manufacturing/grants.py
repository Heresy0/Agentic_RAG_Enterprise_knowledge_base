"""Explicit, sub-checked local grant changes; caller owns the transaction."""
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import select

from ..audit_service import AuditService
from ..db.business_models import BusinessScopeGrant
from ..db.models import Tenant, User
from .contracts import Contract, Identifier


class SubjectGrant(Contract):
    user_id: UUID
    external_subject: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=255)]
    plant_code: Identifier
    permissions: tuple[Literal["view", "review"], ...] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def permissions_are_explicit(self):
        if len(set(self.permissions)) != len(self.permissions):
            raise ValueError("duplicate business permission")
        if not self.external_subject.strip():
            raise ValueError("external subject is required")
        return self


class BusinessGrantConfig(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    schema_version: Literal["business-grants-v1"]
    tenant_id: UUID
    source_system_id: Identifier
    grants: tuple[SubjectGrant, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_subject_plants(self):
        keys = [(r.user_id, r.plant_code) for r in self.grants]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate user and plant grant")
        return self


def preview_grants(config: BusinessGrantConfig, operation="grant"):
    if operation not in ("grant", "revoke"):
        raise ValueError("unknown grant operation")
    if operation == "grant" and any("review" in r.permissions and "view" not in r.permissions for r in config.grants):
        raise ValueError("审核授权必须显式包含读取权限。")
    return dict(operation=operation, users=len({r.user_id for r in config.grants}),
        plants=len({r.plant_code for r in config.grants}),
        requested_permissions=sum(len(r.permissions) for r in config.grants),
        apply=False, database_accessed=False)


def apply_grants(session, config: BusinessGrantConfig, *, operation="grant"):
    preview = preview_grants(config, operation)
    if session.scalar(select(Tenant.id).where(Tenant.id == config.tenant_id, Tenant.status == "active").with_for_update()) is None:
        raise ValueError("目标租户不存在或已停用。")
    # Validate the entire requested identity set before changing the first grant.
    for entry in config.grants:
        user = session.scalar(select(User.id).where(User.id == entry.user_id,
            User.tenant_id == config.tenant_id, User.external_subject == entry.external_subject,
            User.status == "active"))
        if user is None:
            raise ValueError("用户、租户或Keycloak sub不匹配。")
    changed = 0
    for entry in config.grants:
        permissions = set(entry.permissions)
        if operation == "revoke" and "view" in permissions:
            permissions.add("review")
        for permission in sorted(permissions):
            key = (config.tenant_id, entry.user_id, config.source_system_id, entry.plant_code, permission)
            existing = session.get(BusinessScopeGrant, key)
            if operation == "grant" and existing is None:
                session.add(BusinessScopeGrant(tenant_id=config.tenant_id, user_id=entry.user_id,
                    source_system_id=config.source_system_id, plant_code=entry.plant_code, permission=permission))
                changed += 1
            elif operation == "revoke" and existing is not None:
                session.delete(existing)
                changed += 1
    AuditService(session).add(tenant_id=config.tenant_id, actor_user_id=None,
        action="business.grants_" + ("added" if operation == "grant" else "revoked"),
        resource_type="business_source", resource_id=config.source_system_id, outcome="success",
        request_id=uuid4().hex,
        details=dict(requested=preview["requested_permissions"], used=changed, reason="local_admin_cli"))
    return dict(preview, apply=True, database_accessed=True, changed=changed)
