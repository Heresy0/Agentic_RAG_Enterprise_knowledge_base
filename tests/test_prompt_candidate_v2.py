import json
from pathlib import Path

import pytest

from scripts import compare_manufacturing_prompt_candidate_v2 as candidate


def test_v2_example_matches_every_unchanged_live_document_schema():
    text=candidate.CANDIDATE.read_text(encoding='utf-8')
    example=json.loads(text.split('输出：\n',1)[1])
    from jsonschema import Draft202012Validator
    from backend.app.knowledge.answer_models import EvidenceFirstAnswerDraft
    from backend.app.knowledge.answer_coverage import coverage_errors
    draft=EvidenceFirstAnswerDraft.model_validate(example)
    assert not coverage_errors(draft,'设备K的校验周期是什么，台账能证明本期已完成吗？')
    inputs=candidate.experiment.read(candidate.experiment.PRIOR/'inputs.json')
    for entry in inputs:
        payload=entry['payload']
        if candidate.experiment.MARKER in payload['messages'][0]['content']:
            Draft202012Validator(payload['response_format']['json_schema']['schema']).validate(example)


def test_v2_only_replaces_system_prefix(monkeypatch):
    monkeypatch.setattr(candidate.experiment,'CANDIDATE',candidate.CANDIDATE)
    inputs=candidate.experiment.read(candidate.experiment.PRIOR/'inputs.json')
    checked=0
    for entry in inputs:
        old=entry['payload'];marker=candidate.experiment.MARKER
        if marker not in old['messages'][0]['content']:continue
        new=candidate.experiment.candidate_payload(old)
        assert new['messages'][1:]==old['messages'][1:]
        assert {k:v for k,v in new.items() if k!='messages'}=={k:v for k,v in old.items() if k!='messages'}
        assert new['messages'][0]['content'].split(marker)[1]==old['messages'][0]['content'].split(marker)[1]
        checked+=1
    assert checked==9


def test_v2_freeze_keeps_old_evidence_and_new_entry_protected(tmp_path):
    candidate.freeze(tmp_path/'new-run')
    plan=candidate.experiment.read(tmp_path/'new-run/plan.json')
    assert plan['candidate_version']=='v2'
    assert plan['candidate_sha256']==candidate.experiment.digest(candidate.CANDIDATE)
    assert 'scripts/compare_manufacturing_prompt_candidate_v2.py' in plan['protected']
    assert 'evals/manufacturing_prompt_candidate_v1/candidate-system.txt' in plan['protected']
    assert 'evals/manufacturing_prompt_candidate_v1/runs/20261114-r16/trials.jsonl' in plan['protected']
    assert plan['limits']['max_model_calls']==54
    with pytest.raises(FileExistsError):candidate.freeze(tmp_path/'new-run')
