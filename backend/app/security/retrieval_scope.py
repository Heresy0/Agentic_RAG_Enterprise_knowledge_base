from dataclasses import dataclass
from uuid import UUID
from sqlalchemy import true
from ..manufacturing.scope import ManufacturingFence


@dataclass(frozen=True, slots=True)
class RetrievalScope:
    """后端授权生成的不可变范围；None 表示跨库模式，不表示无限制。"""

    tenant_id: UUID
    knowledge_base_id: UUID | None = None
    knowledge_base_ids: frozenset[UUID] = frozenset()
    knowledge_base_names: tuple[tuple[UUID, str], ...] = ()
    manufacturing: ManufacturingFence | None = None

    def __post_init__(self):
        ids = frozenset(self.knowledge_base_ids)
        if self.knowledge_base_id is not None:
            if ids and ids != {self.knowledge_base_id}:
                raise ValueError("单库范围不能包含其他知识库。")
            ids = frozenset({self.knowledge_base_id})
        object.__setattr__(self, "knowledge_base_ids", ids)
        object.__setattr__(self, "knowledge_base_names", tuple(self.knowledge_base_names))

    def knowledge_base_filter(self, column):
        # 空集合生成恒假的 IN 条件，绝不退化为全租户搜索。
        if self.knowledge_base_id is not None:
            return column == self.knowledge_base_id
        return column.in_(sorted(self.knowledge_base_ids, key=str))

    def contains(self, tenant_id, knowledge_base_id):
        try:
            return UUID(str(tenant_id)) == self.tenant_id and UUID(str(knowledge_base_id)) in self.knowledge_base_ids
        except (TypeError, ValueError):
            return False

    def document_filter(self):
        return self.manufacturing.predicate() if self.manufacturing is not None else true()

    def require_current_manufacturing(self, session):
        if self.manufacturing is not None:
            self.manufacturing.require_current(session, self)

    def contains_document(self, metadata):
        return (self.contains(metadata.get("tenant_id"), metadata.get("knowledge_base_id"))
                and (self.manufacturing is None or self.manufacturing.contains_metadata(metadata)))
