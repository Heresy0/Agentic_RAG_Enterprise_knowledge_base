"""Persist paired turns; serialize requests without holding a DB lock during LLM calls."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from ..db.conversation_models import Conversation, ConversationTurn
from ..security.principal import Principal
from ..security.retrieval_scope import RetrievalScope
from .answer_models import AnswerResult
from .conversation_memory import empty_state, valid_state
from ..security.authorization import AuthorizationDenied, AuthorizationService

HISTORY_TURNS = 6
LEASE_SECONDS = 240


class ConversationNotFound(Exception):
    pass


class ConversationBusy(Exception):
    pass


class ConversationStore:
    def __init__(self, session: Session, principal: Principal, scope: RetrievalScope):
        if principal.tenant_id != scope.tenant_id:
            raise ConversationNotFound("对话不存在。")
        self.session, self.principal, self.scope = session, principal, scope

    def _owner(self):
        return (
            Conversation.tenant_id == self.scope.tenant_id,
            Conversation.user_id == self.principal.user_id,
            Conversation.knowledge_base_id == self.scope.knowledge_base_id,
        )

    def get(self, conversation_id: UUID) -> Conversation:
        row = self.session.scalar(select(Conversation).where(*self._owner(), Conversation.id == conversation_id)
                                  .execution_options(populate_existing=True))
        if row is None:
            raise ConversationNotFound("对话不存在或不属于当前用户和知识库。")
        return row

    def create(self, *, commit=True) -> Conversation:
        row = Conversation(tenant_id=self.scope.tenant_id, user_id=self.principal.user_id, knowledge_base_id=self.scope.knowledge_base_id)
        self.session.add(row)
        self.session.flush()
        if commit:
            self.session.commit()
        self.session.refresh(row)
        return row

    def list(self, limit=20, offset=0):
        return list(self.session.scalars(select(Conversation).where(*self._owner()).order_by(Conversation.updated_at.desc(), Conversation.id.desc()).limit(limit).offset(offset)))

    def _turn_is_readable(self, turn):
        # 全范围模式保存整个候选范围，保守覆盖引用遗漏及追问的间接依赖。
        ids = turn.authorized_knowledge_base_ids
        if not ids and turn.knowledge_base_id is not None:
            ids = [str(turn.knowledge_base_id)]  # 迁移前的单库历史。
        try:
            return bool(ids) and {UUID(value) for value in ids} <= self.scope.knowledge_base_ids
        except (TypeError, ValueError):
            return False

    def safe_title(self, row):
        state = self.context_memory(row.id)
        first = self.session.scalar(select(ConversationTurn).where(
            ConversationTurn.tenant_id == self.scope.tenant_id,
            ConversationTurn.conversation_id == row.id,
            ConversationTurn.turn_index == 1,
        ))
        cutoff = state.cutoff
        return row.title if not cutoff and (first is None or self._turn_is_readable(first)) else "历史权限已变更"

    def _checkpoint(self, row, *, token=None):
        """A privacy reset survives failed QA; it stores no new user/model content."""
        state = empty_state(self.principal, self.scope, cutoff=row.turn_count)
        expected_token = token if token is not None else row.lease_token
        changed = self.session.execute(update(Conversation).where(
            *self._owner(), Conversation.id == row.id, Conversation.turn_count == row.turn_count,
            Conversation.lease_token == expected_token,
        ).values(memory_json=state.model_dump(mode='json')).execution_options(synchronize_session=False)).rowcount
        if changed != 1:
            self.session.rollback()
            raise ConversationBusy('对话已改变，请重新加载后重试。')
        self.session.commit()
        return state

    def ensure_authorized(self, conversation_id, token):
        # Fresh SQL reads, fixed request scope; never add newly granted bases mid-request.
        readable = AuthorizationService(self.session).list_readable_knowledge_base_ids(principal=self.principal)
        if not self.scope.knowledge_base_ids or not self.scope.knowledge_base_ids <= readable:
            self._checkpoint(self.get(conversation_id), token=token)
            raise AuthorizationDenied('知识库访问权限已变更，请重新提问。')

    def context_memory(self, conversation_id, *, token=None):
        row = self.get(conversation_id)
        state = None
        if row.memory_json is not None:
            try:
                state = valid_state(row.memory_json, self.principal, self.scope, row.turn_count)
                if not {UUID(value) for value in state.dependency_ids} <= self.scope.knowledge_base_ids:
                    raise ValueError('memory scope revoked')
                # Reverify persisted quotes against original user input, never rewritten questions.
                indices = {i.source.turn for i in state.items}
                originals = dict(self.session.execute(select(ConversationTurn.turn_index, ConversationTurn.question).where(
                    ConversationTurn.tenant_id == self.scope.tenant_id,
                    ConversationTurn.conversation_id == conversation_id,
                    ConversationTurn.turn_index.in_(indices),
                )).all())
                if any(i.source.quote not in originals.get(i.source.turn, '') for i in state.items):
                    raise ValueError('memory source changed')
            except (ValueError, TypeError):
                return self._checkpoint(row, token=token)
        cutoff = state.cutoff if state is not None else 0
        # Metadata only: also detect a revoked prefix outside the six-turn prompt window.
        snapshots = self.session.execute(select(ConversationTurn.turn_index,
            ConversationTurn.authorized_knowledge_base_ids, ConversationTurn.knowledge_base_id).where(
                ConversationTurn.tenant_id == self.scope.tenant_id,
                ConversationTurn.conversation_id == conversation_id,
                ConversationTurn.turn_index > cutoff,
            )).all()
        if any(not self._turn_is_readable(t) for t in snapshots):
            return self._checkpoint(row, token=token)
        if state is None:
            return empty_state(self.principal, self.scope, coverage_start=max(1, row.turn_count - HISTORY_TURNS + 1))
        if state.covered_through < max(cutoff, row.turn_count - HISTORY_TURNS):
            # Feature-off gap is too old to replay safely; bootstrap only the readable window.
            state = self._checkpoint(row, token=token)
        return state

    def turns(self, conversation_id: UUID, *, limit=100, before=None, for_context=False):
        row = self.get(conversation_id)
        # Reading a revoked history also records a reset, so regrant cannot revive old context.
        state = self.context_memory(conversation_id) if not for_context else None
        cutoff = state.cutoff if state else (row.memory_json or {}).get('cutoff', 0)
        statement = select(ConversationTurn).where(
            ConversationTurn.tenant_id == self.scope.tenant_id,
            ConversationTurn.knowledge_base_id == self.scope.knowledge_base_id,
            ConversationTurn.conversation_id == conversation_id,
        )
        if before is not None:
            statement = statement.where(ConversationTurn.turn_index < before)
        rows = list(self.session.scalars(statement.order_by(ConversationTurn.turn_index.desc()).limit(limit)))
        rows.reverse()
        if for_context:
            rows = [t for t in rows if t.turn_index > cutoff]
            # 失效轮次之前的上下文也不再沿用，避免跨越权限变化继续旧话题。
            start = 0
            for i, turn in enumerate(rows):
                if not self._turn_is_readable(turn):
                    start = i + 1
            return rows[start:]
        return [turn if turn.turn_index > cutoff and self._turn_is_readable(turn) else SimpleNamespace(
            turn_index=turn.turn_index, question="历史内容已隐藏：访问权限发生变化。",
            retrieval_question="", created_at=turn.created_at,
            result_json=AnswerResult(answer="该轮历史不再可访问，请重新提问。", answerable=False,
                                     citations=[], refusal_reason="知识库访问权限已变更。").model_dump(mode="json"),
        ) for turn in rows]

    def acquire(self, conversation_id: UUID) -> str:
        """原子抢占有超时的租约，模型调用期间不持有数据库行锁。"""
        self.get(conversation_id)
        token = uuid4().hex
        now = datetime.now(timezone.utc)
        changed = self.session.execute(update(Conversation).where(
            *self._owner(), Conversation.id == conversation_id,
            or_(Conversation.lease_token.is_(None), Conversation.lease_expires_at <= now),
        ).values(lease_token=token, lease_expires_at=now + timedelta(seconds=LEASE_SECONDS)).execution_options(synchronize_session=False)).rowcount
        if changed != 1:
            self.session.rollback()
            raise ConversationBusy("这段对话正在回答另一个问题，请稍后重试。")
        self.session.commit()
        return token

    def append(self, conversation_id: UUID, token: str, question: str, retrieval_question: str, result: AnswerResult,
               *, commit=True, memory=None, expected_cutoff=None):
        """同时写入问题和回答；过期或被替换的租约不能提交结果。"""
        row = self.get(conversation_id)
        index = row.turn_count + 1
        predicates = [] if expected_cutoff is None else [
            func.coalesce(Conversation.memory_json['cutoff'].as_integer(), 0) == expected_cutoff]
        values = {} if memory is None else {'memory_json': memory.model_dump(mode='json')}
        changed = self.session.execute(update(Conversation).where(
            *self._owner(), Conversation.id == conversation_id,
            Conversation.lease_token == token,
            Conversation.lease_expires_at > datetime.now(timezone.utc),
            Conversation.turn_count == index - 1,
            *predicates,
        ).values(turn_count=index, title=question[:200] if index == 1 else row.title,
                 lease_token=None, lease_expires_at=None, updated_at=datetime.now(timezone.utc), **values)
            .execution_options(synchronize_session=False)).rowcount
        if changed != 1:
            self.session.rollback()
            raise ConversationBusy("对话已改变或处理超时，请重新加载后重试。")
        self.session.add(ConversationTurn(
            tenant_id=self.scope.tenant_id, knowledge_base_id=self.scope.knowledge_base_id,
            conversation_id=conversation_id, turn_index=index, question=question,
            retrieval_question=retrieval_question, result_json=result.model_dump(mode="json"),
            authorized_knowledge_base_ids=sorted(map(str, self.scope.knowledge_base_ids)),
        ))
        self.session.flush()
        if commit:
            self.session.commit()

    def release(self, conversation_id: UUID, token: str):
        # Roll back failed retrieval/storage transactions before releasing only our lease.
        self.session.rollback()
        self.session.execute(update(Conversation).where(*self._owner(), Conversation.id == conversation_id,
            Conversation.lease_token == token).values(lease_token=None, lease_expires_at=None)
            .execution_options(synchronize_session=False))
        self.session.commit()

    def delete(self, conversation_id: UUID, *, commit=True):
        self.get(conversation_id)
        changed = self.session.execute(delete(Conversation).where(*self._owner(), Conversation.id == conversation_id,
            or_(Conversation.lease_token.is_(None), Conversation.lease_expires_at <= datetime.now(timezone.utc)))
            .execution_options(synchronize_session=False)).rowcount
        if changed != 1:
            self.session.rollback()
            raise ConversationBusy("对话正在生成答案，暂时不能删除。")
        if commit:
            self.session.commit()


def invalidate_owned_context(session, principal, knowledge_base_id, conversation_id=None):
    """Record an observed authorization denial without reading/returning old content."""
    statement = select(Conversation).where(Conversation.tenant_id == principal.tenant_id,
        Conversation.user_id == principal.user_id, Conversation.knowledge_base_id == knowledge_base_id)
    if conversation_id is not None:
        statement = statement.where(Conversation.id == conversation_id)
    scope = RetrievalScope(principal.tenant_id, knowledge_base_id)
    store = ConversationStore(session, principal, scope)
    for row in session.scalars(statement).all():
        store._checkpoint(row)
