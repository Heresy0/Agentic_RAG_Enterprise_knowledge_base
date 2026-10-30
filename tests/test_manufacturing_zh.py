"""Chinese corpus provenance, frozen splits and fail-closed acquisition checks."""
from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from scripts import build_manufacturing_zh as builder
from scripts.build_manufacturing_zh_questions import build,build_cases
from scripts.manufacturing_zh_sources import SOURCES
from scripts.validate_manufacturing_public import inventory
from scripts.validate_manufacturing_zh import validate_freeze

ROOT=Path(__file__).resolve().parents[1]
CORPUS=ROOT/'sample_docs/manufacturing_zh_v3'
EVAL=ROOT/'evals/manufacturing_zh_v3'


@pytest.mark.parametrize('url',[
    'http://scjg.jl.gov.cn/test.pdf','https://scjg.jl.gov.cn.attacker.example/test.pdf',
    'https://evilscjg.jl.gov.cn/test.pdf','https://localhost/test.pdf',
    'https://user@scjg.jl.gov.cn/test.pdf','https://scjg.jl.gov.cn:444/test.pdf',
    'file:///test.pdf',
])
def test_only_reviewed_official_https_hosts_are_used(url):
    with pytest.raises(ValueError,match='official HTTPS'):
        builder.checked_url(url)


class Reply:
    def __init__(self,redirect=None,chunks=()):
        self.is_redirect=redirect is not None
        self.headers={'Location':redirect} if redirect else {}
        self.chunks=chunks
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def raise_for_status(self): pass
    def iter_content(self,size): return iter(self.chunks)


def test_redirect_and_size_limit_fail_before_archiving_untrusted_bytes(monkeypatch):
    calls=[]
    def get(url,**kwargs):
        calls.append(url)
        assert kwargs['allow_redirects'] is False and 'verify' not in kwargs
        return Reply(redirect='https://attacker.example/internal')
    monkeypatch.setattr(builder.requests,'get',get)
    with pytest.raises(ValueError): builder.fetch(SOURCES[0]['source_url'])
    assert len(calls)==1
    monkeypatch.setattr(builder,'MAX_BYTES',4)
    monkeypatch.setattr(builder.requests,'get',lambda *a,**k:Reply(chunks=[b'123',b'45']))
    with pytest.raises(ValueError,match='upload limit'): builder.fetch(SOURCES[0]['source_url'])


def test_native_html_conversion_excludes_navigation_and_keeps_merged_table_meaning():
    paragraph='生产单位应根据具体使用情况和产品风险进行分析。'*30
    raw=('<html><body><nav>无关导航</nav><div class="TRS_Editor"><script>execute()</script>'
         '<p>'+paragraph+'</p><table><tr><th>项目</th><th>限制</th></tr>'
         '<tr><td rowspan="2">温度</td><td>≤20℃</td></tr><tr><td>不大于85%</td></tr></table>'
         '</div></body></html>').encode()
    text=builder.html_to_markdown(raw,SOURCES[1])
    assert '无关导航' not in text and 'execute()' not in text
    assert '| 温度 | ≤20℃ |' in text and '| 温度 | 不大于85% |' in text
    assert 'NIST' not in text and '保留原始 HTML' in text
    with pytest.raises(ValueError,match='incomplete'):
        builder.html_to_markdown(b'<html><div class="TRS_Editor">Only download links</div></html>',SOURCES[1])


def test_no_corpus_or_gold_outputs_can_write_into_existing_baselines(tmp_path):
    with pytest.raises(ValueError,match='baseline'):
        builder.build(tmp_path,ROOT/'sample_docs/manufacturing_lite/new-edition',tmp_path/'lock.json')
    with pytest.raises(ValueError,match='outside the corpus'):
        build(CORPUS,CORPUS/'documents/leaked-evals',tmp_path/'visual.json')
    with pytest.raises(ValueError,match='never overwritten'):
        build(CORPUS,EVAL,EVAL/'visual-review.json')


def test_chinese_originals_are_distinct_and_converted_web_originals_are_retained():
    manifest=inventory(CORPUS)
    assert manifest['total_documents']==18 and manifest['synthetic_documents']==0
    assert manifest['counts']=={'pdf':16,'md':2}
    assert manifest['total_pdf_pages']==286
    assert len({s['raw_sha256'] for s in manifest['documents']})==18
    lock=json.loads((ROOT/'sample_docs/manufacturing_zh_v3.sources.json').read_text(encoding='utf-8'))
    assert len(lock['sources'])==18 and lock['deferred']
    for source in manifest['documents']:
        assert source['language']=='zh-CN'
        builder.checked_url(source['source_url'])
        if source['format']=='pdf':
            assert source['raw_sha256']==source['sha256']
        else:
            original=CORPUS/'originals'/(source['id']+'.html')
            assert builder.sha(original.read_bytes())==source['raw_sha256']
    assert not list((CORPUS/'documents').rglob('*json*'))


def test_frozen_chinese_cases_reproduce_from_verified_originals_and_isolated_families():
    frozen,cases,annotations=validate_freeze(EVAL,CORPUS)
    visual=json.loads((EVAL/'visual-review.json').read_text(encoding='utf-8'))
    rebuilt,new_annotations=build_cases(CORPUS,visual)
    assert cases==rebuilt['development']+rebuilt['test'] and annotations==new_annotations
    assert frozen['counts']=={'development':40,'test':80}
    assert sum(c['answerable'] for c in cases)==90
    assert sum(c.get('expected_behavior')=='refuse' for c in cases)==18
    assert sum(c.get('expected_behavior')=='clarify' for c in cases)==12
    assert len(visual['pages'])==44
    assert all(a['evidence_locators'] for a in annotations if a['id'].startswith('MFGZH-Q'))
    baseline_ids={json.loads(line)['id'] for line in (ROOT/'evals/manufacturing_public_v2/development/single_turn.jsonl').read_text(encoding='utf-8').splitlines()}
    assert not baseline_ids.intersection(c['id'] for c in cases)


@pytest.mark.parametrize('target',['development/single_turn.jsonl','test/single_turn.jsonl','annotations.json','visual-review.json'])
def test_modified_frozen_data_or_visual_truth_is_rejected(tmp_path,target):
    destination=tmp_path/'evaluation'
    shutil.copytree(EVAL,destination)
    with (destination/target).open('ab') as stream: stream.write(b'\n')
    with pytest.raises(ValueError,match='Frozen'): validate_freeze(destination,CORPUS)


def test_visual_scan_evidence_cannot_silently_change_its_original_fingerprint():
    visual=json.loads((EVAL/'visual-review.json').read_text(encoding='utf-8'))
    changed=deepcopy(visual)
    next(p for p in changed['pages'] if p['source_id']=='MFG-ZH11')['source_sha256']='0'*64
    with pytest.raises(ValueError,match='Original evidence not verified'): build_cases(CORPUS,changed)
