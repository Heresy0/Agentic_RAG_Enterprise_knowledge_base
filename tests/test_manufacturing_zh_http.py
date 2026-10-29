"""Frozen Chinese selection and source-aware preconditions before paid QA."""
import hashlib
import json
from pathlib import Path

import pytest

from backend.app.knowledge.rag import CHUNKING_VERSION
from scripts.run_manufacturing_http_acceptance import check_zh_inventory, select_cases
from scripts.validate_manufacturing_public import parser_fingerprint

ROOT=Path(__file__).resolve().parents[1]
EVAL=ROOT/'evals/manufacturing_zh_v3'


def test_chinese_development_keeps_all_40_original_cases_and_no_ocr_questions():
    dataset,cases,frozen=select_cases(EVAL,split='development',max_questions=40)
    assert len(cases)==40 and sum(c['answerable'] for c in cases)==30
    assert dataset==EVAL/'development/single_turn.jsonl'
    assert frozen['edition']=='manufacturing-zh-v3'
    manifest=json.loads((ROOT/'sample_docs/manufacturing_zh_v3/manifest.json').read_text(encoding='utf-8'))
    ocr={Path(d['file']).name for d in manifest['documents'] if 'OCR必需' in d['features']}
    assert not any(set(c['required_source_documents']) & ocr for c in cases)


def test_chinese_budget_cannot_silently_expand_to_test_or_drop_questions():
    with pytest.raises(ValueError):
        select_cases(EVAL,split='development',max_questions=39)
    with pytest.raises(ValueError):
        select_cases(EVAL,split='development',case_ids=['MFGZH-Q011'],max_questions=40)


def setup_inventory(tmp_path, *, bad_dev=False, tamper=None):
    manifest=json.loads((ROOT/'sample_docs/manufacturing_zh_v3/manifest.json').read_text(encoding='utf-8'))
    _,cases,frozen=select_cases(EVAL,split='development',max_questions=40)
    groups={'公共参考':'public','生产工艺':'process','质量检验':'quality'}
    rows=[];proof=[]
    for source in manifest['documents']:
        name=Path(source['file']).name
        status='failed' if source['id']=='MFG-ZH11' or (bad_dev and source['id']=='MFG-ZH01') else 'ready'
        row=dict(id=source['id'],file_name=name,status=status)
        rows.append(row)
        proof.append(dict(document_id=row['id'],file_name=name,status=status,sha256=source['sha256'],knowledge_base_id='public'))
    audit=dict(tenant_id='isolated',chunking_version=CHUNKING_VERSION,parser_sha256=parser_fingerprint(),all_checks_passed=True,records=proof)
    if tamper=='version':audit['chunking_version']='structured-v5'
    if tamper=='source':audit['records'][0]['sha256']='0'*64
    path=tmp_path/'audit.json';path.write_text(json.dumps(audit),encoding='utf-8')
    config=dict(tenant_id='isolated',corpus_manifest_sha256=frozen['corpus_manifest_sha256'],index_audit_file=str(path),
                index_audit_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    class HTTP:
        def get(self,url,timeout):
            self.items=rows if '/public/' in url else []
            return self
        def raise_for_status(self):pass
        def json(self):return {'items':self.items}
    return HTTP(),groups,frozen,config,cases,path


def test_failed_test_only_scan_is_visible_but_does_not_block_native_development(tmp_path):
    http,groups,frozen,config,cases,_=setup_inventory(tmp_path)
    check_zh_inventory(http,'http://127.0.0.1:8000',groups,frozen,config,cases)


def test_failed_selected_source_blocks_paid_run(tmp_path):
    http,groups,frozen,config,cases,_=setup_inventory(tmp_path,bad_dev=True)
    with pytest.raises(ValueError,match='unavailable source'):
        check_zh_inventory(http,'http://127.0.0.1:8000',groups,frozen,config,cases)


@pytest.mark.parametrize('tamper',['version','source','fingerprint'])
def test_audit_changes_and_wrong_source_or_version_block_paid_run(tmp_path,tamper):
    http,groups,frozen,config,cases,path=setup_inventory(tmp_path,tamper=tamper)
    if tamper=='fingerprint':path.write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError):
        check_zh_inventory(http,'http://127.0.0.1:8000',groups,frozen,config,cases)
