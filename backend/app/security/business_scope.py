"""Business scope comes from active, sub-bound users and explicit database grants."""
from dataclasses import dataclass
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.business_models import BusinessScopeGrant
from ..db.models import Tenant, User
from ..manufacturing.contracts import Identifier, WorkOrderRecord
from .principal import Principal

_identifier = TypeAdapter(Identifier)


class BusinessAuthorizationDenied(PermissionError):
    def __init__(self):
        super().__init__("当前业务范围不可访问。")


@dataclass(frozen=True, slots=True)
class BusinessScope:
    tenant_id: UUID
    user_id: UUID
    source_system_id: str
    plant_codes: frozenset[str]
    permission: str = "view"

    def __post_init__(self):
        if not isinstance(self.tenant_id, UUID) or not isinstance(self.user_id, UUID):
            raise ValueError("scope identities must be UUIDs")
        _identifier.validate_python(self.source_system_id)
        plants = frozenset(self.plant_codes)
        if len(plants) > 100 or self.permission not in ("view", "review"):
            raise ValueError("invalid business scope")
        for plant in plants:
            _identifier.validate_python(plant)
        object.__setattr__(self, "plant_codes", plants)

    def contains(self, tenant_id, source_system_id, plant_code) -> bool:
        try:
            return (UUID(str(tenant_id)) == self.tenant_id and source_system_id == self.source_system_id
                    and plant_code in self.plant_codes)
        except (ValueError, TypeError):
            return False

    def contains_record(self, record: WorkOrderRecord) -> bool:
        return self.contains(record.tenant_id, record.source_system_id, record.plant_code)


class BusinessAuthorizationService:
    def __init__(self, session: Session):
        self.session = session

    def require_scope(self, *, principal: Principal, source_system_id: str, permission="view") -> BusinessScope:
        _identifier.validate_python(source_system_id)
        if permission not in ("view", "review"):
            raise ValueError("unknown business permission")
        active = self.session.scalar(select(User.id).join(Tenant, Tenant.id == User.tenant_id).where(
            User.id == principal.user_id, User.tenant_id == principal.tenant_id,
            User.external_subject == principal.external_subject, User.status == "active", Tenant.status == "active"))
        if active is None:
            raise BusinessAuthorizationDenied()
        rows = self.session.execute(select(BusinessScopeGrant.plant_code, BusinessScopeGrant.permission).where(
            BusinessScopeGrant.tenant_id == principal.tenant_id, BusinessScopeGrant.user_id == principal.user_id,
            BusinessScopeGrant.source_system_id == source_system_id)).all()
        view = {r.plant_code for r in rows if r.permission == "view"}
        allowed = view if permission == "view" else view & {r.plant_code for r in rows if r.permission == "review"}
        if not allowed:
            raise BusinessAuthorizationDenied()
        return BusinessScope(principal.tenant_id, principal.user_id, source_system_id, frozenset(allowed), permission)

    def require_current(self, *, principal: Principal, scope: BusinessScope) -> BusinessScope:
        if principal.tenant_id != scope.tenant_id or principal.user_id != scope.user_id:
            raise BusinessAuthorizationDenied()
        current = self.require_scope(principal=principal, source_system_id=scope.source_system_id,
                                     permission=scope.permission)
        if not scope.plant_codes <= current.plant_codes:
            raise BusinessAuthorizationDenied()
        return current
