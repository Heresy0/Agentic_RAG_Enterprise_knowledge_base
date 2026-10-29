"""Apply reviewed scoring annotations without changing questions or old packs."""
from copy import deepcopy
import json
import hashlib


def apply_scoring_rules(cases, path):
    if not path.is_file():
        return cases
    rules = json.loads(path.read_text(encoding='utf-8'))
    if rules.get('version') not in ('2026-10-16-r7', '2026-10-16-r8', '2026-10-16-r9', '2026-10-16-r10', '2026-10-29-r12'):
        raise ValueError('Unsupported scoring annotation version')
    allowed = {'required_facts', 'optional_facts', 'fact_alternatives',
               'fact_assertions', 'accepted_evidence_sets'}
    patches = rules['cases']
    if set(patches) - {case['id'] for case in cases}:
        raise ValueError('Scoring annotations contain unknown case IDs')
    result = deepcopy(cases)
    for case in result:
        patch = patches.get(case['id'], {})
        if patch and rules.get('version') == '2026-10-29-r12':
            expected = rules.get('source_question_sha256', {}).get(case['id'])
            if expected != hashlib.sha256(case['question'].encode()).hexdigest():
                raise ValueError('Reviewed scoring annotation question fingerprint differs')
        if set(patch) - allowed:
            raise ValueError('Scoring annotations cannot change questions or access scopes')
        for key, value in patch.items():
            if key in ('fact_alternatives', 'fact_assertions'):
                case[key] = {**case.get(key, {}), **value}
            else:
                case[key] = value
    return result
