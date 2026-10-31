"""Immutable work-order bindings over the existing atomic turn/lease store."""
from sqlalchemy import select

from ..db.business_models import ConversationBusinessBinding
from ..knowledge.conversations import ConversationStore, ConversationNotFound
from ..integrations.mes import MESError, MESContextChanged
from .history_access import invalidate_binding, invalidate_revoked_conversations


class ManufacturingConversationStore(ConversationStore):
    def __init__(self, session, principal, scope):
        super().__init__(session, principal, scope, manufacturing=True)

    def _checkpoint(self, row, *, token=None):
        # This tombstone must survive a later memory/lease CAS failure.
        invalidate_binding(self.session, self.principal, row.id)
        return super()._checkpoint(row, token=token)

    def create_bound(self, context):
        row = self.create(commit=False)
        record = context.record
        self.session.add(ConversationBusinessBinding(tenant_id=self.principal.tenant_id, conversation_id=row.id,
            source_system_id=record.source_system_id, plant_code=record.plant_code, work_order_id=record.work_order_id,
            source_revision=record.source_revision, payload_sha256=context.payload_sha256))
        self.session.flush()
        return row

    def binding(self, identifier):
        invalidate_revoked_conversations(self.session, self.principal, conversation_id=identifier)
        self.get(identifier)
        row = self.session.scalar(select(ConversationBusinessBinding).where(
            ConversationBusinessBinding.tenant_id == self.principal.tenant_id,
            ConversationBusinessBinding.conversation_id == identifier).execution_options(populate_existing=True))
        if row is None:
            raise ConversationNotFound("制造对话不存在。")
        return row

    def check_binding(self, identifier, context):
        binding = self.binding(identifier)
        record = context.record
        if (binding.invalidated_at is not None or (binding.source_system_id, binding.plant_code, binding.work_order_id)
                != (record.source_system_id, record.plant_code, record.work_order_id)):
            raise MESContextChanged()
        if binding.source_revision != record.source_revision:
            invalidate_binding(self.session, self.principal, identifier)
            raise MESContextChanged()
        if binding.payload_sha256 != context.payload_sha256:
            invalidate_binding(self.session, self.principal, identifier)
            raise MESError()
