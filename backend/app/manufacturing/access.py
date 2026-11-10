"""Database-authorized work-order access shared by future HTTP and QA entry points."""
from ..security.business_scope import BusinessAuthorizationDenied, BusinessAuthorizationService


class WorkOrderAccessService:
    def __init__(self, session, connector):
        self.authorization = BusinessAuthorizationService(session)
        self.connector = connector

    def scope(self, principal):
        return self.authorization.require_scope(principal=principal,
            source_system_id=self.connector.config.source_system_id)

    async def read_context(self, *, principal, work_order_id, expected_source_revision=None, operation_id=None, job_card_id=None, batch_id=None, step_id=None):
        scope = self.scope(principal)
        try:
            return await self.connector.read_context(work_order_id, scope=scope,
                expected_source_revision=expected_source_revision, **({key: value for key, value in
                dict(operation_id=operation_id, job_card_id=job_card_id,batch_id=batch_id,step_id=step_id).items() if value is not None}))
        finally:
            self.authorization.require_current(principal=principal, scope=scope)

    async def list_work_orders(self, *, principal, cursor=None, limit=20):
        scope = self.scope(principal)
        try:
            return await self.connector.list_work_orders(scope=scope, cursor=cursor, limit=limit)
        finally:
            self.authorization.require_current(principal=principal, scope=scope)

    async def verify_context(self, *, principal, previous):
        scope = self.scope(principal)
        if not scope.contains_record(previous.record):
            raise BusinessAuthorizationDenied()
        try:
            return await self.connector.verify_context(previous, scope=scope)
        finally:
            self.authorization.require_current(principal=principal, scope=scope)

    async def list_operations(self, *, principal, work_order_id):
        scope = self.scope(principal)
        try:
            return await self.connector.list_operations(work_order_id, scope=scope)
        finally:
            self.authorization.require_current(principal=principal, scope=scope)

    async def list_job_cards(self, *, principal, work_order_id, operation_id, cursor=None, limit=20):
        scope = self.scope(principal)
        try:
            return await self.connector.list_job_cards(work_order_id, operation_id, scope=scope, cursor=cursor, limit=limit)
        finally:
            self.authorization.require_current(principal=principal, scope=scope)
