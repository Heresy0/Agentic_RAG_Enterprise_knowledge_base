"""All model paths bind references to the current request, without changing facts."""
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from test_answer_service import build_service, make_document
from test_answer_repair import draft, repair_service, execute
from test_rag_quality_fixes import calc_draft
from backend.app.knowledge.answer_repair import AnswerRepair
from backend.app.knowledge.context_builder import ContextBuilder
from backend.app.knowledge.output_contract import live_generation_schema
from backend.app.knowledge.rag_budget import RagBudget, rag_budget_scope


def context():
    return ContextBuilder().build([make_document('温度是80摄氏度。',chunk_id='temperature'),
        make_document('任务持续2分钟。',chunk_id='duration')])


@pytest.mark.parametrize('ref',['1','[资料1]','data_1','data:资料1','https://example.com','资料999',']}]},'])
def test_ordinary_live_contract_rejects_nonexistent_reference_strings(ref):
    schema=live_generation_schema(context())
    with pytest.raises(ValidationError):schema.model_validate(draft(citation=ref,indices=(1,)).model_dump())


def test_current_reference_pool_preserves_calculation_contract():
    ctx=context();schema=live_generation_schema(ctx)
    data=calc_draft().model_dump();data['claims'][0]['citations']=['资料2']
    data['claims'][0]['calculation']['inputs'][1]['source']='资料2'
    value=schema.model_validate(data)
    assert value.claims[0].calculation.inputs[1].unit=='秒'
    assert value.claims[0].calculation.inputs[1].source=='资料2'
    ctx.items.pop()
    with pytest.raises(ValidationError):live_generation_schema(ctx).model_validate(data)


@pytest.mark.parametrize('stage',['generation','regeneration','schema_recovery'])
def test_live_model_paths_use_the_current_reference_pool(monkeypatch,stage):
    service,*_=build_service(monkeypatch,model_result={});schemas=[];ctx=context()
    def structured(schema,**_):
        schemas.append(schema)
        return SimpleNamespace(invoke=lambda _:schema.model_validate(draft(indices=(1,)).model_dump()))
    factory=lambda **_:SimpleNamespace(with_structured_output=structured)
    service._base_llm=factory();service._regeneration_model_factory=factory
    # Full schema recovery obeys the shared budget, not the small-edit byte cap.
    service._settings.rag_repair_enabled=True;service._settings.rag_repair_prompt_bytes=1
    with rag_budget_scope(RagBudget(max_tokens=64000)) as budget:
        if stage=='generation':
            result=service._generate_draft(question='温度是多少？',context=ctx)
        elif stage=='regeneration':
            budget.begin_feedback()
            result=service._regenerate_draft(question='温度是多少？',context=ctx)
        else:
            repair=AnswerRepair(service._settings,model_factory=factory,
                guard_factory=lambda *_:SimpleNamespace(available=True,require=lambda:None))
            result=repair.recover_schema(messages=service._generation_messages('温度是多少？',ctx),
                context=ctx,scope=None,session=None,refresh_context=lambda:True)
    assert len(schemas)==1 and result.claims[0].text=='温度是80摄氏度'
    assert result.claims[0].citations==['资料1']
    with pytest.raises(ValidationError):schemas[0].model_validate(draft(citation='资料3').model_dump())


def test_normal_binding_repair_uses_the_same_reference_pool():
    repair,calls,_=repair_service(draft(indices=(1,)));factory=repair.model_factory;schemas=[]
    def model(**kwargs):
        original=factory(**kwargs)
        def structured(schema,**opts):
            schemas.append(schema)
            return original.with_structured_output(schema,**opts)
        return SimpleNamespace(with_structured_output=structured)
    repair.model_factory=model
    result,_,_=execute(repair,draft())
    assert len(calls)==1 and result.coverage[0].claim_indices==[1]
    with pytest.raises(ValidationError):schemas[0].model_validate(draft(citation='资料2').model_dump())


@pytest.mark.parametrize('limits',[dict(max_calls=0),dict(max_tokens=1),dict(max_seconds=0)])
def test_full_schema_recovery_still_stops_at_each_shared_budget_limit(limits):
    repair,calls,_=repair_service(draft(indices=(1,)));repair.settings.rag_repair_prompt_bytes=1
    with rag_budget_scope(RagBudget(**limits)) as budget:
        result=repair.recover_schema(messages=[],context=context(),scope=None,session=None,refresh_context=lambda:True)
    assert result is None and calls==[] and budget.calls==0
    assert budget.limited_by in ('calls','tokens','time')
