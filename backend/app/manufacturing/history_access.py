"""Current business authorization also applies to every historical read path."""
from datetime import datetime, timezone

from sqlalchemy import and_, exists, or_, select, update

from ..db.business_models import BusinessScopeGrant, ConversationBusinessBinding, QARunBusinessScope
from ..db.conversation_models import Conversation
from ..db.quality_models import QARun


def grant_exists(principal, binding, *, permission="view"):
    return exists(select(BusinessScopeGrant.user_id).where(
        BusinessScopeGrant.tenant_id == principal.tenant_id,
        BusinessScopeGrant.user_id == principal.user_id,
        BusinessScopeGrant.source_system_id == binding.source_system_id,
        BusinessScopeGrant.plant_code == binding.plant_code,
        BusinessScopeGrant.permission == permission)).correlate(binding)


def conversation_binding_exists():
    return exists(select(ConversationBusinessBinding.conversation_id).where(
        ConversationBusinessBinding.tenant_id == Conversation.tenant_id,
        ConversationBusinessBinding.conversation_id == Conversation.id))


def conversation_allowed(principal):
    binding = ConversationBusinessBinding
    return ~exists(select(binding.conversation_id).where(binding.tenant_id == Conversation.tenant_id,
        binding.conversation_id == Conversation.id, ~grant_exists(principal, binding)))


def run_business_allowed(principal, *, review=False):
    binding = QARunBusinessScope
    allowed = grant_exists(principal, binding)
    if review:
        allowed = and_(allowed, grant_exists(principal, binding, permission="review"))
    return ~exists(select(binding.run_id).where(binding.tenant_id == QARun.tenant_id,
        binding.run_id == QARun.id, ~allowed))


def invalidate_revoked_conversations(session, principal, *, conversation_id=None):
    binding = ConversationBusinessBinding
    owned = select(Conversation.id).where(Conversation.tenant_id == principal.tenant_id,
        Conversation.user_id == principal.user_id)
    if conversation_id is not None:
        owned = owned.where(Conversation.id == conversation_id)
    changed = session.execute(update(binding).where(binding.tenant_id == principal.tenant_id,
        binding.conversation_id.in_(owned), binding.invalidated_at.is_(None),
        ~grant_exists(principal, binding)).values(invalidated_at=datetime.now(timezone.utc))
        .execution_options(synchronize_session=False)).rowcount
    if changed:
        session.commit()


def invalidate_binding(session, principal, identifier):
    owned = select(Conversation.id).where(Conversation.tenant_id == principal.tenant_id,
        Conversation.user_id == principal.user_id, Conversation.id == identifier)
    session.execute(update(ConversationBusinessBinding).where(
        ConversationBusinessBinding.tenant_id == principal.tenant_id,
        ConversationBusinessBinding.conversation_id.in_(owned)).values(invalidated_at=datetime.now(timezone.utc))
        .execution_options(synchronize_session=False))
    session.commit()


def invalidate_scope_bindings(session, principal, knowledge_base_id):
    owned = select(Conversation.id).where(Conversation.tenant_id == principal.tenant_id,
        Conversation.user_id == principal.user_id, Conversation.knowledge_base_id == knowledge_base_id)
    session.execute(update(ConversationBusinessBinding).where(
        ConversationBusinessBinding.tenant_id == principal.tenant_id,
        ConversationBusinessBinding.conversation_id.in_(owned)).values(invalidated_at=datetime.now(timezone.utc))
        .execution_options(synchronize_session=False))
    session.commit()
