"""Server-only active fixture registry. Archived files never authorize a new run."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field

from .contracts import Contract, Digest, Identifier, canonical_digest
from .execution_contracts import selection_kwargs
from .source_registration import LIVE_SOURCE_CONTRACT, LiveSourceDescriptor
from ..integrations.mes import MESConnector, MESError
from ..integrations.mes_contracts import load_source_configs
from ..security.business_scope import BusinessAuthorizationService

CONNECTOR_CONTRACT = "mes-read-only-v1:fixture-v1"


class FixtureEntry(Contract):
    tenant_id: UUID
    source_system_id: Identifier
    fixture_version: Identifier
    fixture_sha256: Digest


class FixtureRegistry(Contract):
    schema_version: Literal["manufacturing-evaluation-registry-v1"]
    active: tuple[FixtureEntry, ...] = Field(min_length=1, max_length=20)


class LiveSourceEntry(Contract):
    tenant_id: UUID
    source_system_id: Identifier
    source_kind: Literal['live']
    source_contract: Literal['erpnext-read-only-v1:execution-v1']
    source_sha256: Digest


class EvaluationRegistry(Contract):
    schema_version: Literal['manufacturing-evaluation-registry-v2']
    active: tuple[FixtureEntry | LiveSourceEntry, ...] = Field(min_length=1, max_length=20)


class EvaluationSources:
    def __init__(self, settings, *, connector_factory=MESConnector):
        self.settings, self.connector_factory = settings, connector_factory

    def registered(self, tenant_id):
        try:
            path = Path(self.settings.manufacturing_evaluation_registry)
            if path.stat().st_size > 65536:
                raise ValueError()
            data = json.loads(path.read_bytes())
            model = EvaluationRegistry if data.get('schema_version') == 'manufacturing-evaluation-registry-v2' else FixtureRegistry
            registry = model.model_validate(data)
            if len({entry.tenant_id for entry in registry.active}) != len(registry.active):
                raise ValueError()
            entry = next(row for row in registry.active if row.tenant_id == tenant_id)
            config = load_source_configs(Path(self.settings.manufacturing_sources_file))[tenant_id]
            if config.source_system_id != entry.source_system_id:
                raise ValueError()
            if isinstance(entry, LiveSourceEntry) and not config.execution_selection:
                raise ValueError()
            binding = dict(**entry.model_dump(mode="json"), connector_contract=LIVE_SOURCE_CONTRACT if isinstance(entry, LiveSourceEntry) else CONNECTOR_CONTRACT,
                endpoint_sha256=canonical_digest(config.base_url))
            return config, binding
        except (OSError, ValueError, TypeError, AttributeError, StopIteration, KeyError):
            raise MESError() from None

    async def verify_source(self, connector, scope, binding):
        if binding.get('source_kind') == 'live':
            payload, _ = await connector._get('/v1/source', scope=scope)
            expected = LiveSourceDescriptor(tenant_id=binding['tenant_id'], source_system_id=binding['source_system_id'],
                source_contract=binding['source_contract'], configuration_sha256=binding['source_sha256'])
            if payload != expected.model_dump(mode='json'):
                raise MESError()
            return
        payload, _ = await connector._get("/v1/fixture", scope=scope)
        expected = {key: binding[key] for key in ("tenant_id", "source_system_id", "fixture_version", "fixture_sha256")}
        if payload != dict(schema_version="mes-fixture-v1", **expected):
            raise MESError()

    async def inspect(self, session, principal, *, expected=None, context=None):
        config, binding = self.registered(principal.tenant_id)
        if expected is not None and binding != expected:
            raise MESError()
        if binding.get('source_kind') == 'live' and context is None:
            raise MESError()  # A live source always requires an exact record, not just its descriptor.
        access = BusinessAuthorizationService(session)
        scope = access.require_scope(principal=principal, source_system_id=config.source_system_id, permission="review")
        if context is not None and not scope.contains_record(context.record):
            raise MESError()
        async with self.connector_factory(config) as connector:
            await self.verify_source(connector, scope, binding)
            if context is not None:
                current = await connector.read_context(context.record.work_order_id, scope=scope,
                    expected_source_revision=context.record.source_revision, **selection_kwargs(context.record))
                if current.payload_sha256 != context.payload_sha256:
                    raise MESError()
                await self.verify_source(connector, scope, binding)
        access.require_current(principal=principal, scope=scope)
        if self.registered(principal.tenant_id)[1] != binding:
            raise MESError()
        return binding

    def check(self, session, principal, *, expected=None, context=None):
        # Review endpoints and the quality Worker are synchronous, and each call
        # owns its client/event loop. No shared AsyncClient crosses loop boundaries.
        def run(): return asyncio.run(self.inspect(session, principal, expected=expected, context=context))
        try: asyncio.get_running_loop()
        except RuntimeError: return run()
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(run).result()


def registry_for(settings):
    return getattr(settings, "manufacturing_evaluation_sources", None) or EvaluationSources(settings)
