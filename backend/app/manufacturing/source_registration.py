"""Identity of the configured live ERP projection, never a synthetic fixture."""
from typing import Literal
from uuid import UUID
from .contracts import Contract, Digest, Identifier, canonical_digest

LIVE_SOURCE_CONTRACT = 'erpnext-read-only-v1:execution-v1'
OPENMES_SOURCE_CONTRACT = 'openmes-read-only-v1:qa-v1'


class LiveSourceDescriptor(Contract):
    schema_version: Literal['erpnext-live-source-v1'] = 'erpnext-live-source-v1'
    source_kind: Literal['live'] = 'live'
    tenant_id: UUID
    source_system_id: Identifier
    source_contract: Literal['erpnext-read-only-v1:execution-v1'] = LIVE_SOURCE_CONTRACT
    configuration_sha256: Digest


class OpenMESSourceDescriptor(Contract):
    schema_version: Literal['openmes-live-source-v1'] = 'openmes-live-source-v1'
    source_kind: Literal['live'] = 'live'
    tenant_id: UUID
    source_system_id: Identifier
    source_contract: Literal['openmes-read-only-v1:qa-v1'] = OPENMES_SOURCE_CONTRACT
    configuration_sha256: Digest


def erpnext_source_descriptor(config):
    return LiveSourceDescriptor(tenant_id=config.tenant_id, source_system_id=config.source_system_id,
        configuration_sha256=canonical_digest(dict(endpoint=config.base_url.rstrip('/'),
            company_plants=config.company_plants, timezone=config.site_timezone, contract=LIVE_SOURCE_CONTRACT)))
