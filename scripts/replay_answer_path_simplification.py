"""Compare deterministic answer paths using closed traces; never run QA or models."""
import argparse
import ast
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import subprocess

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.diagnose_manufacturing_answer_focus_regressions import (
    frozen_context, manufacturing_answer_context,
)
from backend.app.knowledge.answer_models import EvidenceFirstAnswerDraft
from backend.app.knowledge.answer_service import AnswerService
from backend.app.knowledge.answer_validation import AnswerValidator
from backend.app.knowledge.output_contract import (
    live_generation_schema, prepare_draft, validate_output,
)
from backend.app.manufacturing.answer_context import attach_business_context
from backend.app.manufacturing.generation_models import precision_projection
from backend.app.manufacturing.generation_selection import GroundedDraft, grounded_schema

RUNS = (
    'manufacturing-gold-100-v4-grounded-live-20261113-r5',
    'manufacturing-gold-100-v4-answer-focus-live-20261113-r8',
    'manufacturing-answer-contract-revised-full-live-20261113-r11',
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    default=str).encode()).hexdigest()


def observe(draft, context, question):
    validation = validate_output(draft, validator=AnswerValidator(), context=context, question=question)
    result = (AnswerService._render_result(None, draft=draft, context=context, question=question)
              if validation.valid and draft.answerable else
              AnswerService._unanswerable(draft) if validation.valid else None)
    return dict(draft=digest(draft.model_dump(mode='json', exclude_none=True)),
                valid=validation.valid, errors=validation.errors,
                result=digest(result.model_dump(mode='json')) if result else None)


def capture():
    cases = {c['id']: c for c in map(json.loads, (ROOT/'evals/manufacturing_gold_100_v4/cases.jsonl')
                                   .read_text(encoding='utf-8').splitlines())}
    protected = {p.relative_to(ROOT).as_posix(): sha(p)
                 for p in (ROOT/'evals/manufacturing_gold_100_v4').glob('*') if p.is_file()}
    rows, skipped = [], []
    for run in RUNS:
        directory = ROOT/'tmp'/run
        for key, case in cases.items():
            path = directory/(key+'-result.json')
            protected[path.relative_to(ROOT).as_posix()] = sha(path)
            record = json.loads(path.read_text(encoding='utf-8'))
            event = next((e for e in record['events'] if e['method'] == 'POST'
                          and e['path'] in ('/api/qa', '/api/manufacturing/qa')), None)
            if event is None:
                skipped.append(dict(run=run, case_id=key, reason='no_qa_post'))
                continue
            stages = event['trace']['stages']
            generations = stages.get('answer_generation', [])
            if not generations:
                skipped.append(dict(run=run, case_id=key, reason='no_saved_draft'))
                continue
            # Reconstruct this POST, not a later conversation GET.
            context, active = frozen_context(dict(record, final=event), case['question'])
            with manufacturing_answer_context(active):
                generation = generations[0]
                decision = next(v for v in stages['answer_decisions'] if v['stage']=='generation')
                cls = GroundedDraft if active else EvidenceFirstAnswerDraft
                draft = cls.model_validate(dict(answerable=decision['answerable'],
                    response_type='answer' if decision['answerable'] else 'refuse',
                    claims=generation['claims'], coverage=generation['coverage']['coverage']))
                prepared = prepare_draft(draft, context)
                model_context = attach_business_context(context, question=case['question']) if active else context
                messages = AnswerService._generation_messages(None, case['question'], model_context)
                projection = precision_projection(case['question'], context)
                projection = prepare_draft(projection, context) if projection is not None else None
                row = dict(run=run, case_id=key, saved=observe(prepared, context, case['question']),
                    messages=digest([(type(m).__name__, m.content) for m in messages]),
                    generation_schema=digest(live_generation_schema(model_context).model_json_schema()),
                    projection=observe(projection, context, case['question']) if projection is not None else None)
                if active:
                    row['repair_schema'] = digest(grounded_schema(model_context).model_json_schema())
                    row['decision_schema'] = digest(grounded_schema(model_context, claim=True).model_json_schema())
                rows.append(row)
    # The complete historical result directories, including prior grades, stay byte-identical.
    for pattern in ('manufacturing_gold_100_grounded_live_v1',
                    'manufacturing_gold_100_answer_focus_live_v1',
                    'manufacturing_answer_contract_full_live_v1'):
        for path in (ROOT/'evals'/pattern/'runs').rglob('*'):
            if path.is_file():
                protected[path.relative_to(ROOT).as_posix()] = sha(path)
    return dict(rows=rows, skipped=skipped, protected=protected, model_calls=0,
                limitation='Saved processed drafts, not raw supplier responses. Refusal/clarify metadata absent '
                           'from traces is not reconstructed. No new semantic grade or live authorization claim.')


def projection_equivalence():
    """Exercise both implementations on frozen business contexts, without changing records."""
    from backend.app.manufacturing import generation_models
    from backend.app.manufacturing.answer_context import facts_only_question, comparison_question
    from backend.app.knowledge.context_builder import ContextBuilder
    from scripts.manufacturing_legacy_drafts import prepare_legacy_draft
    source = subprocess.check_output(['git', 'show', 'faf72ad:backend/app/manufacturing/generation_models.py'],
                                     cwd=ROOT).decode('utf-8')
    names = {'batch_start_projection', 'execution_start_projection', 'precision_projection'}
    tree = ast.parse(source)
    functions = ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names],
                           type_ignores=[])
    namespace = dict(generation_models.__dict__)
    exec(compile(functions, '<frozen-projections-faf72ad>', 'exec'), namespace)
    questions = ('工单实际开工时间是什么？', '工序实际开工时间是什么？', '作业卡实际开工时间是什么？',
        '批次实际开工时间是什么？', '步骤实际开工时间是什么？', '所选卡何时开工？',
        '所选工序的工序代码、登记状态和实际开工时间分别是什么？',
        '所选作业卡的编号、状态和实际开工时间分别是什么？', '实际开工几点几分？',
        '工单和作业卡的实际开工分别是什么？')
    cases = {c['id']: c for c in map(json.loads, (ROOT/'evals/manufacturing_gold_100_v4/cases.jsonl')
                                   .read_text(encoding='utf-8').splitlines())}
    count = projected = clarified = 0
    for run in RUNS:
        for key, case in cases.items():
            record = json.loads((ROOT/'tmp'/run/(key+'-result.json')).read_text(encoding='utf-8'))
            event = next((e for e in record['events'] if e['method']=='POST'
                          and e['path']=='/api/manufacturing/qa'
                          and e['trace']['stages'].get('answer_generation')), None)
            if event is None:
                continue
            _, active = frozen_context(dict(record, final=event), case['question'])
            variants = [(active, question) for question in questions]
            record = active.context.record
            execution = getattr(record, 'execution', None)
            if execution is not None:
                no_card = execution.model_copy(update={name: None for name in type(execution).model_fields
                                                       if name.startswith('job_card_')})
                unselected = active.context.model_copy(update={
                    'record': record.model_copy(update={'execution': no_card})})
                variants.append((replace(active, context=unselected), '作业卡实际开工时间是什么？'))
            if record.schema_version == 'openmes-work-order-v1':
                unselected = active.context.model_copy(update={
                    'record': record.model_copy(update={'batch': None, 'step': None})})
                variants.extend((replace(active, context=unselected), question)
                                for question in ('批次实际开工时间是什么？', '步骤实际开工时间是什么？'))
            for candidate, question in variants:
                selected = replace(candidate, facts_only=facts_only_question(question),
                                   comparison=comparison_question(question))
                with manufacturing_answer_context(selected):
                    context = ContextBuilder().build([], question=question)
                    before = namespace['precision_projection'](question, context)
                    after = precision_projection(question, context)
                    assert (before is None) == (after is None), (run, key, question)
                    count += 1
                    if before is None:
                        continue
                    before = prepare_legacy_draft(before, context)
                    after = prepare_draft(after, context)
                    assert observe(before, context, question) == observe(after, context, question), (run, key, question)
                    projected += 1
                    clarified += after.response_type == 'clarify'
    return dict(questions_checked=count, projected=projected, clarified=clarified,
                baseline='faf72ad', equivalent=True, model_calls=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--compare', type=Path)
    args = parser.parse_args()
    output = args.out.resolve()
    output.relative_to(ROOT/'tmp')
    output.parent.mkdir(parents=True, exist_ok=True)
    result = capture()
    if args.compare:
        before = json.loads(args.compare.read_text(encoding='utf-8'))
        assert before['protected'] == result['protected'], 'Historical inputs changed'
        assert before['rows'] == result['rows'], 'Deterministic behavior changed'
        assert before['skipped'] == result['skipped']
        result['equivalent'] = True
        result['projection_matrix'] = projection_equivalence()
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(dict(replayed=len(result['rows']), projections=sum(r['projection'] is not None
        for r in result['rows']), skipped=len(result['skipped']), protected=len(result['protected']),
        equivalent=result.get('equivalent'), model_calls=0), ensure_ascii=False))


if __name__ == '__main__':
    main()
