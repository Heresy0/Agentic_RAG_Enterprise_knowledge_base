"""Small authenticated MES source; authorization precedes pagination and lookup."""
import base64
from dataclasses import dataclass, field
import hashlib
import hmac
import json
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from pydantic import SecretStr, TypeAdapter

from backend.app.manufacturing.contracts import Identifier, WorkOrderFixtures, canonical_digest
from backend.app.integrations.mes_contracts import WorkOrderPage


@dataclass(frozen=True, slots=True)
class SourceCredential:
    tenant_id: UUID
    source_system_id: str
    plant_codes: frozenset[str]
    token: SecretStr = field(repr=False)

    def __post_init__(self):
        if not isinstance(self.tenant_id, UUID) or not isinstance(self.token, SecretStr):
            raise ValueError("invalid source credential type")
        token = self.token.get_secret_value()
        if not 32 <= len(token) <= 512 or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise ValueError("invalid source credential")
        adapter = TypeAdapter(Identifier)
        adapter.validate_python(self.source_system_id)
        plants = frozenset(self.plant_codes)
        if not plants or len(plants) > 100:
            raise ValueError("invalid source grant")
        for plant in plants:
            adapter.validate_python(plant)
        object.__setattr__(self, "plant_codes", plants)


def _b64(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def create_app(fixtures: WorkOrderFixtures, credentials: tuple[SourceCredential, ...]) -> FastAPI:
    if not credentials or len(credentials) > 20:
        raise ValueError("source credentials required")
    keys = [c.token.get_secret_value() for c in credentials]
    if len(set(keys)) != len(keys) or any(c.tenant_id != fixtures.tenant_id or
            c.source_system_id != fixtures.source_system_id for c in credentials):
        raise ValueError("source credential identity mismatch")
    records = {r.work_order_id: r for r in fixtures.records}
    fixtures_hash = canonical_digest(fixtures.model_dump(mode="json"))
    app = FastAPI(title="只读模拟MES", docs_url=None, redoc_url=None, openapi_url=None)

    def authorized(authorization: str | None = Header(default=None),
                   tenant: str | None = Header(default=None, alias="X-MES-Tenant"),
                   plants: str | None = Header(default=None, alias="X-MES-Plants")):
        supplied = authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
        if len(supplied) > 512 or not supplied.isascii():
            raise HTTPException(401, "源调用身份无效。")
        credential = None
        for configured in credentials:
            if hmac.compare_digest(supplied, configured.token.get_secret_value()):
                credential = configured
        if credential is None:
            raise HTTPException(401, "源调用身份无效。")
        try:
            parsed_tenant = UUID(tenant or "")
            if plants is None or len(plants) > 6500:
                raise ValueError()
            parsed_plants = frozenset(plants.split(","))
            if (parsed_tenant != credential.tenant_id or not parsed_plants or
                    not parsed_plants <= credential.plant_codes):
                raise ValueError()
        except ValueError:
            raise HTTPException(404, "业务范围不存在或不可访问。") from None
        return credential, parsed_plants

    @app.get("/health")
    def health():
        return dict(status="ok", mode="synthetic_read_only")

    @app.get("/v1/work-orders", response_model=WorkOrderPage)
    def list_work_orders(response: Response, scope=Depends(authorized),
                         limit: int = Query(20, ge=1, le=20),
                         cursor: str | None = Query(None, min_length=1, max_length=1024)):
        credential, plants = scope
        response.headers["Cache-Control"] = "no-store"
        binding = canonical_digest(dict(tenant=str(credential.tenant_id), source=credential.source_system_id,
            plants=sorted(plants), fixture=fixtures_hash, limit=limit))
        last = ""
        if cursor is not None:
            try:
                payload, signature = cursor.split(".")
                expected = hmac.new(credential.token.get_secret_value().encode(), payload.encode(), hashlib.sha256).hexdigest()
                if not hmac.compare_digest(signature, expected):
                    raise ValueError()
                decoded = json.loads(base64.b64decode(payload + "=" * (-len(payload) % 4), altchars=b"-_", validate=True))
                if decoded["binding"] != binding or not isinstance(decoded["last"], str):
                    raise ValueError()
                last = decoded["last"]
            except (ValueError, KeyError, TypeError, UnicodeError):
                raise HTTPException(422, "分页游标无效或业务范围已变化。") from None
        visible = sorted((r for r in records.values() if r.tenant_id == credential.tenant_id
            and r.source_system_id == credential.source_system_id and r.plant_code in plants
            and r.work_order_id > last), key=lambda r: r.work_order_id)
        selected = visible[:limit]
        next_cursor = None
        if len(visible) > limit:
            payload = _b64(json.dumps(dict(binding=binding, last=selected[-1].work_order_id),
                                     sort_keys=True, separators=(",", ":")).encode())
            signature = hmac.new(credential.token.get_secret_value().encode(), payload.encode(), hashlib.sha256).hexdigest()
            next_cursor = payload + "." + signature
        return WorkOrderPage(items=tuple(selected), next_cursor=next_cursor)

    @app.get("/v1/work-orders/{work_order_id}/context")
    def context(work_order_id: Identifier, response: Response, scope=Depends(authorized)):
        credential, plants = scope
        record = records.get(work_order_id)
        if (record is None or record.tenant_id != credential.tenant_id or
                record.source_system_id != credential.source_system_id or record.plant_code not in plants):
            raise HTTPException(404, "工单不存在或不可访问。")
        response.headers["Cache-Control"] = "no-store"
        return record.model_dump(mode="json")

    return app
