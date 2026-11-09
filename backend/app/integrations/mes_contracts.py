"""MES transport contracts kept separate from M0's frozen source schemas."""
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID
import json
import os

from pydantic import ConfigDict, Field, SecretStr, StringConstraints, model_validator

from ..manufacturing.contracts import Contract, Identifier, WorkOrderRecord


class MESConfigurationError(ValueError):
    pass


class MESSourceConfig(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    source_system_id: Identifier
    tenant_id: UUID
    base_url: Annotated[str, StringConstraints(strict=True, max_length=500)]
    allowed_plants: tuple[Identifier, ...] = Field(min_length=1, max_length=100)
    token: SecretStr = Field(repr=False)
    connect_timeout: float = Field(default=2, gt=0, le=2)
    call_timeout: float = Field(default=5, gt=0, le=5)
    max_response_bytes: int = Field(default=65536, ge=128, le=65536, strict=True)
    max_context_characters: int = Field(default=4000, ge=128, le=4000, strict=True)

    @model_validator(mode="after")
    def validate_source(self):
        parts = urlsplit(self.base_url)
        if (parts.scheme not in ("http", "https") or not parts.hostname or
                parts.username is not None or parts.password is not None or parts.query or parts.fragment or
                parts.path not in ("", "/") or "\\" in self.base_url or any(c.isspace() for c in self.base_url)):
            raise ValueError("invalid source URL")
        _ = parts.port
        if parts.scheme == "http" and parts.hostname not in ("127.0.0.1", "localhost", "::1", "mock-mes", "erpnext-adapter"):
            raise ValueError("plain HTTP is limited to loopback and the explicit local source services")
        token = self.token.get_secret_value()
        if not 32 <= len(token) <= 512 or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise ValueError("invalid source credential")
        if len(set(self.allowed_plants)) != len(self.allowed_plants):
            raise ValueError("duplicate configured plant")
        return self


class MESSourceSpec(Contract):
    source_system_id: Identifier
    tenant_id: UUID
    base_url: Annotated[str, StringConstraints(strict=True, max_length=500)]
    allowed_plants: tuple[Identifier, ...] = Field(min_length=1, max_length=100)
    token_env: Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Z][A-Z0-9_]{2,79}$")]


class MESSourcesFile(Contract):
    schema_version: str
    sources: tuple[MESSourceSpec, ...] = Field(min_length=1, max_length=20)


def load_source_configs(path: Path, environ=None) -> dict[UUID, MESSourceConfig]:
    """Only explicit server configuration may choose endpoints or credentials."""
    environ = os.environ if environ is None else environ
    try:
        if path.stat().st_size > 65536:
            raise ValueError("oversized configuration")
        data = MESSourcesFile.model_validate(json.loads(path.read_bytes()))
        if data.schema_version != "mes-sources-v1":
            raise ValueError("unknown configuration")
        configs = {}
        for spec in data.sources:
            if spec.tenant_id in configs:
                raise ValueError("M1 allows one MES source per tenant")
            configs[spec.tenant_id] = MESSourceConfig.model_validate(dict(
                **spec.model_dump(exclude={"token_env"}), token=environ[spec.token_env]))
        return configs
    except (ValueError, KeyError, OSError):
        # Never propagate Pydantic's input dictionary or a source credential.
        raise MESConfigurationError("MES源配置无效或凭据未配置。") from None


class WorkOrderPage(Contract):
    items: tuple[WorkOrderRecord, ...] = Field(max_length=20)
    next_cursor: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=1024)] | None = None

    @model_validator(mode="after")
    def unique_records(self):
        if (len({r.work_order_id for r in self.items}) != len(self.items) or
                len({r.source_record_id for r in self.items}) != len(self.items)):
            raise ValueError("duplicate page item")
        return self
