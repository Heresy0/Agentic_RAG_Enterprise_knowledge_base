"""User-sourced query state, never evidence. Models propose; code verifies/merges."""
import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_ITEMS = 16
MAX_CONFLICTS = 4
MAX_BYTES = 8192
MAX_PROMPT_BYTES = 36000
CORRECTION = re.compile(r'更正|纠正|改为|改成|改查|改看|不再|取消|以.{0,20}为准|之前.{0,20}(?:错|不对)|不是.{0,40}而是')
HYPOTHETICAL = re.compile(r'假设|假如|如果|是否|是不是|[？?]|吗(?:[，。！!]|$)')
NEGATED_CORRECTION = re.compile(r'(?:不要|不能|不得|不应|不必).{0,8}(?:更正|纠正|改为|改成|取消)|(?:假设|假如|如果).{0,20}(?:更正|改为|改成|取消)')
NUMBER = re.compile(r'\d+(?:\.\d+)?|[零〇一二三四五六七八九十百千万亿两]+(?=\s*(?:元|块|小时|分钟|秒|件|份|个|次|%|％))')
BUSINESS_VALUE = re.compile(r'[-+]?(?:\d+(?:\.\d+)?|[零〇一二三四五六七八九十百千万亿两点]+)\s*(?:亿元|万元|元|块|小时|分钟|秒|件|份|个|次|%|％)')
TEMPORAL = re.compile(r'(?:19|20)\d{2}年(?:\d{1,2}月(?:\d{1,2}[日号])?)?|(?:19|20)\d{2}[-/]\d{1,2}(?:[-/]\d{1,2})?')


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Source(StrictModel):
    turn: int = Field(strict=True, ge=1)
    quote: str = Field(min_length=1, max_length=240)


class Proposal(Source):
    object: str = Field(min_length=1, max_length=80)
    qualifier: str = Field(default='', max_length=80, description='不同业务时期、版本、场景用不同 qualifier；普通查询年份变更用 time 字段')
    field: Literal['subject', 'time', 'version', 'filter', 'exclusion', 'exception', 'claim']
    value: str = Field(min_length=1, max_length=160, description='必须是 quote 中的连续原文')
    operation: Literal['set', 'replace'] = 'set'


class MemoryResolution(StrictModel):
    question: str = Field(min_length=1, max_length=1000)
    updates: list[Proposal] = Field(default_factory=list, max_length=16)
    relevant_keys: list[str] = Field(default_factory=list, max_length=16)
    needs_clarification: bool = False


class Item(StrictModel):
    key: str
    object: str = Field(min_length=1, max_length=80)
    qualifier: str = Field(max_length=80)
    field: Literal['subject', 'time', 'version', 'filter', 'exclusion', 'exception', 'claim']
    value: str = Field(min_length=1, max_length=160)
    source: Source
    status: Literal['active', 'superseded', 'conflicted'] = 'active'


class MemoryState(StrictModel):
    version: Literal[1] = 1
    tenant_id: str
    user_id: str
    mode: Literal['single', 'all_accessible']
    knowledge_base_id: str | None
    dependency_ids: list[str]
    cutoff: int = Field(strict=True, ge=0, default=0)
    coverage_start: int = Field(strict=True, ge=1)
    covered_through: int = Field(strict=True, ge=0)
    incomplete: bool = False
    items: list[Item] = Field(default_factory=list, max_length=MAX_ITEMS)


def encoded(state):
    return json.dumps(state.model_dump(mode='json'), ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def empty_state(principal, scope, *, cutoff=0, coverage_start=1):
    return MemoryState(tenant_id=str(principal.tenant_id), user_id=str(principal.user_id),
        mode='single' if scope.knowledge_base_id else 'all_accessible',
        knowledge_base_id=str(scope.knowledge_base_id) if scope.knowledge_base_id else None,
        dependency_ids=sorted(map(str, scope.knowledge_base_ids)), cutoff=cutoff,
        coverage_start=max(cutoff + 1, coverage_start), covered_through=cutoff,
        incomplete=bool(cutoff or coverage_start > 1))


def valid_state(raw, principal, scope, turn_count):
    """Fail closed on malformed, foreign, oversized or future state."""
    state = MemoryState.model_validate(raw)
    expected = empty_state(principal, scope)
    if (state.tenant_id, state.user_id, state.mode, state.knowledge_base_id) != (
            expected.tenant_id, expected.user_id, expected.mode, expected.knowledge_base_id):
        raise ValueError('memory owner mismatch')
    if (len(encoded(state)) > MAX_BYTES or state.cutoff > turn_count
            or state.covered_through > turn_count or state.covered_through < state.cutoff
            or state.coverage_start <= state.cutoff or state.coverage_start > turn_count + 1
            or not state.dependency_ids
            or len({i.key for i in state.items if i.status == 'conflicted'}) > MAX_CONFLICTS):
        raise ValueError('invalid memory bounds')
    for item in state.items:
        if (item.key != item_key(item.object, item.qualifier, item.field)
                or not state.coverage_start <= item.source.turn <= state.covered_through
                or item.value not in item.source.quote):
            raise ValueError('invalid memory source')
    return state


def item_key(obj, qualifier, field):
    return hashlib.sha256(json.dumps([obj, qualifier, field], ensure_ascii=False).encode()).hexdigest()[:16]


def source_context(original, quote):
    # Inspect the containing user sentence too: cropping cannot remove “假设/不要/是否”.
    clauses = [clause for clause in re.split(r'[。；;\n]', original) if quote in clause]
    return '；'.join(clauses) if clauses else original


def merge(state, resolution, questions, current_turn, dependency_ids):
    """All-or-nothing verified delta; never silently lose active conditions."""
    candidate = state.model_copy(deep=True)
    candidate.dependency_ids = sorted(set(state.dependency_ids) | set(dependency_ids))
    candidate.covered_through = current_turn
    for proposal in sorted(resolution.updates, key=lambda p: p.turn):
        original = questions.get(proposal.turn)
        if (original is None or proposal.turn < state.coverage_start
                or proposal.quote not in original or proposal.value not in proposal.quote
                or (proposal.field == 'claim' and HYPOTHETICAL.search(source_context(original, proposal.quote)))):
            raise ValueError('unverified memory proposal')
        # Object must occur in this user's source or be anchored in already verified state.
        if proposal.object not in original and not any(i.object == proposal.object for i in candidate.items):
            raise ValueError('unanchored memory object')
        if proposal.qualifier and proposal.qualifier not in original and not any(
                i.object == proposal.object and i.qualifier == proposal.qualifier for i in candidate.items):
            raise ValueError('unanchored memory qualifier')
        key = item_key(proposal.object, proposal.qualifier, proposal.field)
        previous = [i for i in candidate.items if i.key == key and i.status != 'superseded']
        replacing = proposal.operation == 'replace'
        # New query period can override the old query period; never an asserted business fact.
        fresh_source = proposal.turn > state.covered_through
        time_change = (proposal.field == 'time' and fresh_source
                       and TEMPORAL.search(proposal.quote))
        if any(i.value == proposal.value for i in previous) and not (replacing or time_change):
            continue
        if any(i.source.turn > proposal.turn for i in previous):
            raise ValueError('stale memory update')
        if replacing and (not fresh_source or NEGATED_CORRECTION.search(source_context(original, proposal.quote))
                          or not (CORRECTION.search(proposal.quote) or time_change)):
            raise ValueError('replacement lacks explicit correction')
        if replacing or time_change:
            for item in previous:
                item.status = 'superseded'
        elif previous:
            for item in previous:
                item.status = 'conflicted'
        candidate.items.append(Item(key=key, object=proposal.object, qualifier=proposal.qualifier,
            field=proposal.field, value=proposal.value, source=Source(turn=proposal.turn, quote=proposal.quote),
            status='conflicted' if previous and not (replacing or time_change) else 'active'))
    # Only superseded entries may be compacted; complete history stays in conversation_turns.
    while (len(candidate.items) > MAX_ITEMS or len(encoded(candidate)) > MAX_BYTES) and any(
            i.status == 'superseded' for i in candidate.items):
        candidate.items.remove(next(i for i in candidate.items if i.status == 'superseded'))
    if (len(candidate.items) > MAX_ITEMS or len(encoded(candidate)) > MAX_BYTES
            or len({i.key for i in candidate.items if i.status == 'conflicted'}) > MAX_CONFLICTS):
        raise ValueError('memory capacity exceeded')
    return candidate


def clarification_reason(state, resolution, current, rewritten):
    relevant = set(resolution.relevant_keys)
    known = {i.key for i in state.items}
    if not relevant <= known:
        return '会话条件无法可靠确认，请重新写明查询对象和条件。'
    if resolution.needs_clarification:
        return '问题的指代或条件不明确，请重新写明查询对象和条件。'
    # Include object matches even if the model forgot to mark that key relevant.
    conflicts = [i for i in state.items if i.status == 'conflicted' and (
        i.key in relevant or i.object in current or i.object in rewritten)]
    if conflicts:
        return '本次查询涉及尚未确认的会话条件冲突，请明确采用哪个条件后再查询。'
    # History/summary numbers may not become current-question calculation premises.
    # Date/year filters are allowed only when anchored in a verified time entry.
    allowed = set(NUMBER.findall(current))
    dates = set()
    for item in state.items:
        if item.status == 'active' and item.field == 'time':
            for date in TEMPORAL.findall(item.value):
                dates.add(date)
    numeric_question = TEMPORAL.sub(lambda m: '' if m.group(0) in dates else m.group(0), rewritten)
    # Matching digits is insufficient: a current “2小时” must not authorize historical “2元”.
    normalize = lambda value: re.sub(r'\s+', '', value)
    current_values = {normalize(value) for value in BUSINESS_VALUE.findall(current)}
    if {normalize(value) for value in BUSINESS_VALUE.findall(numeric_question)} - current_values:
        return '请在当前问题中重新写明计算数值及单位；历史陈述和摘要不能充当事实证据。'
    for item in state.items:
        if item.status != 'active' or item.field not in {'subject', 'version'}:
            continue
        value = item.value
        if re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]*\d[A-Za-z0-9_.-]*(?:版)?', value):
            numeric_question = numeric_question.replace(value, '')
        elif item.field == 'version' and re.fullmatch(r'\d+(?:\.\d+)*', value):
            numeric_question = re.sub(r'(?:版本\s*' + re.escape(value) + r'|' + re.escape(value) + r'版)', '', numeric_question)
    if set(NUMBER.findall(numeric_question)) - allowed:
        return '请在当前问题中重新写明日期、版本或计算所需数值；历史陈述和摘要不能充当事实证据。'
    # An unverified historical business assertion cannot be laundered into a query premise.
    for item in state.items:
        if item.field == 'claim' and item.value in rewritten and item.value not in current:
            return '请重新写明需要核实的问题；历史业务陈述需要由知识库证据验证。'
    return None
