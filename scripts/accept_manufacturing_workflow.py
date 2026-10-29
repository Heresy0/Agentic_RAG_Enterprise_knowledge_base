"""Phased real manufacturing QA/feedback/evaluation demo; all reviews are non-independent assistant operations.

Run ask, inspect the saved answers, then samples/evaluate. Inspect the candidate
answers and supply an explicit bound review JSON before verify. Never infer
semantic correctness from a model's own confidence or force a failed comparison closed.
"""
import argparse
import json
from pathlib import Path
import sys
import time
import unicodedata
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.run_manufacturing_http_acceptance import LocalKeycloakSession

TITLE='每月工业产品质量安全调度会议纪要'
QUESTIONS=dict(native='按第75号令《工业产品生产单位落实质量安全主体责任监督管理规定》所收录原文，月调度需形成的会议文件叫什么名称？',
    clarify='校准环境温度应该设多少？',
    followup='我说的是盐雾试验设备，按JJF（吉）92—2021，请说明校准时环境温度。',
    switch='换一个对象：我说的是热工仪表校验仪，按JJF（吉）31—2021，校准环境温度有什么要求？',
    ocr='按《卡规检查仪校准规范》所收录原文，校准环境温度和相对湿度有什么要求？',
    refuse='请查询ERP系统中工单HC-DEMO-20261029的实时完工数量，并更新MES系统。')


def norm(value):return ''.join(unicodedata.normalize('NFKC',value).split())


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase',choices=['ask','samples','evaluate','verify'])
    p.add_argument('--run-dir',type=Path,required=True);p.add_argument('--apply',action='store_true')
    p.add_argument('--allow-model-calls',action='store_true');p.add_argument('--reviews',type=Path)
    p.add_argument('--evidence-dir',type=Path,help='New private evidence directory for a new app version; never reuse a paid run')
    p.add_argument('--ocr-issue-run-id',help='Optional existing bad OCR answer to retain as needs_evidence, never auto approve')
    p.add_argument('--acknowledge-unpriced-usage',action='store_true')
    p.add_argument('--review-note',default='助手执行的非独立流程验收，不代表真实用户反馈、独立人工复核或质量提升。')
    a=p.parse_args(argv);run=a.run_dir.resolve();run.relative_to(ROOT/'tmp')
    if not a.apply:p.error('执行需要--apply')
    if a.phase in ('ask','evaluate') and not a.allow_model_calls:p.error('问答或评测需要--allow-model-calls')
    cfg=json.loads((run/'state.json').read_text(encoding='utf-8'));kb=cfg['knowledge_bases']['公共参考']
    s=LocalKeycloakSession(run/'tokens.json');base='http://127.0.0.1:8000'
    evidence_root=a.evidence_dir.resolve() if a.evidence_dir else run
    evidence_root.relative_to(ROOT/'tmp');evidence_root.mkdir(parents=True,exist_ok=True)
    def read(name):return json.loads((evidence_root/name).read_text(encoding='utf-8'))
    def write(name,data):
        with (evidence_root/name).open('x',encoding='utf-8') as stream:json.dump(data,stream,ensure_ascii=False,indent=2)
    def request(method,path,**kwargs):
        r=s.request(method,base+path,timeout=180,**kwargs);r.raise_for_status()
        return None if r.status_code==204 else r.json()
    if a.phase=='ask':
        if (evidence_root/'workflow-answers.json').exists() or any((evidence_root/('workflow-'+name+'.json')).exists() for name in QUESTIONS):
            raise ValueError('已有完整或部分请求证据，请先检查，不得自动重复付费问答')
        conversation=request('POST',f'/api/knowledge-bases/{kb}/conversations',json={})['id']
        results={}
        for name,question in QUESTIONS.items():
            payload=dict(question=question,knowledge_base_id=kb)
            if name in ('clarify','followup','switch'):payload['conversation_id']=conversation
            response=request('POST','/api/qa',json=payload)
            snapshot=request('GET','/api/qa/runs/'+response['run_id'])
            results[name]=dict(question=question,response=response,snapshot=snapshot)
            # Persist each successful paid request immediately; partial runs are inspected, never silently replayed.
            write('workflow-'+name+'.json',results[name])
            print(json.dumps(dict(case=name,response_type=response.get('response_type'),run_id=response['run_id']),ensure_ascii=False),flush=True)
        write('workflow-answers.json',results)
    elif a.phase=='samples':
        if (evidence_root/'workflow-datasets.json').exists():raise ValueError('样本版本已存在')
        answers=read('workflow-answers.json');datasets={};cases={}
        for role,name in (('fixed','native'),('feedback','clarify')):
            row=answers[name];response=row['response'];run_id=response['run_id']
            if response.get('response_type')!=('answer' if role=='fixed' else 'clarify'):raise ValueError('回答类型未符合待审核用例')
            evidence=[]
            if role=='fixed':
                if norm(TITLE) not in norm(response['answer']):raise ValueError('实际答案未出现待核对的会议文件名称')
                for item in row['snapshot']['snapshot']['context']['items']:
                    if item['document_name'].startswith('MFG-ZH01_') and norm(TITLE) in norm(item['content']) and len(item['content'])<=2000:
                        evidence=[dict(citation_id=item['citation_id'],quote=item['content'])];break
                if not evidence:raise ValueError('未找到绑定当前快照的原文证据')
            request('PUT',f'/api/qa/runs/{run_id}/feedback',json=dict(rating='up',comment=a.review_note,
                share_with_admins=True,allow_dataset_export=True))
            case=request('POST','/api/quality/cases',json=dict(run_id=run_id));case_id=case['case']['id']
            review=dict(expected_revision=case['case']['revision'],review_status='confirmed',sample_status='approved',
                root_cause='other' if role=='fixed' else 'ambiguous',review_reason=a.review_note,
                expected_answerable=role=='fixed',expected_behavior='answer' if role=='fixed' else 'clarify',
                evaluation_question=row['question'],reference_answer=TITLE if role=='fixed' else '先询问设备名称、型号与适用规范。',
                required_facts=[TITLE] if role=='fixed' else [],forbidden_claims=[],evidence=evidence)
            request('PUT','/api/quality/cases/'+case_id,json=review)
            dataset=request('POST','/api/quality/datasets',json=dict(name='制造业闭环流程_'+role,case_ids=[case_id]))
            datasets[role]=dataset['id'];cases[role]=case_id
        improvement=request('POST','/api/quality/improvements',json=dict(case_id=cases['feedback'],
            description='澄清交互与反馈回归闭环流程验收；两次同配置运行仅检查流程与一致性，不声称质量提升。',change_ref='5bcf642',
            max_latency_ratio=1.5,max_token_ratio=1.3))
        write('workflow-datasets.json',dict(datasets=datasets,cases=cases,improvement=improvement,reviewer='assistant_non_independent'))
        if a.ocr_issue_run_id:
            issue_id=a.ocr_issue_run_id
            request('PUT',f'/api/qa/runs/{issue_id}/feedback',json=dict(rating='down',reason='incorrect',
                comment='助手实测发现OCR温度字符损坏且答案复述乱码；保留失败，需核对原件，不猜测修正数值。',share_with_admins=True,allow_dataset_export=False))
            case=request('POST','/api/quality/cases',json=dict(run_id=issue_id))
            issue=request('PUT','/api/quality/cases/'+case['case']['id'],json=dict(expected_revision=case['case']['revision'],
                review_status='needs_evidence',sample_status='draft',root_cause='parsing',
                review_reason='关键温度原文仍有OCR乱码，待人工核对或视觉模型单独实测；不批准回归参考答案。'))
            write('workflow-ocr-open-issue.json',issue)
        print(json.dumps(dict(datasets=datasets,cases=cases),ensure_ascii=False))
    elif a.phase=='evaluate':
        if (evidence_root/'workflow-evaluations.json').exists() or (evidence_root/'workflow-baseline-submitted.json').exists():
            raise ValueError('已有完整或部分评测证据，请先检查，不得自动重复付费评测')
        data=read('workflow-datasets.json');jobs={}
        for name in ('baseline','candidate'):
            job=request('POST','/api/quality/evaluations',json=dict(fixed_dataset_id=data['datasets']['fixed'],
                feedback_dataset_id=data['datasets']['feedback'],allow_model_calls=True,max_cases=2,
                max_model_calls=24,max_tokens=400000,reservation_tokens=32000,max_seconds=240))
            write('workflow-'+name+'-submitted.json',job)
            start=time.monotonic()
            while job['status'] in ('queued','running') and time.monotonic()-start<300:
                time.sleep(2);job=request('GET','/api/quality/evaluations/'+job['id'])
            detail=request('GET','/api/quality/evaluations/'+job['id']);jobs[name]=detail
            write('workflow-'+name+'-result.json',detail)
            print(json.dumps(dict(job=name,id=job['id'],status=job['status'],completed=job['completed_cases'])),flush=True)
            if job['status']!='succeeded':raise ValueError('评测未完成成功，停止后续动作')
        write('workflow-evaluations.json',jobs)
    else:
        if a.reviews is None:p.error('必须先检查候选答案并提供--reviews，内容须绑定sample_id和response_binding')
        jobs=read('workflow-evaluations.json');setup=read('workflow-datasets.json')
        reviews=json.loads(a.reviews.read_text(encoding='utf-8'))
        for row in reviews:
            request('POST',f"/api/quality/evaluations/{jobs['candidate']['id']}/reviews/{row['sample_id']}",
                json=dict(response_binding=row['response_binding'],verdict=row['verdict'],reason=row['reason']))
        comparison=request('GET','/api/quality/comparison',params=dict(baseline_job_id=jobs['baseline']['id'],
            candidate_job_id=jobs['candidate']['id'],target_case_id=setup['cases']['feedback'],acknowledge_unknown_usage=a.acknowledge_unpriced_usage))
        write('workflow-comparison.json',comparison)
        if not comparison['passed']:
            print(json.dumps(dict(closed=False,failures=comparison['failures']),ensure_ascii=False));return 1
        improvement=setup['improvement'];path='/api/quality/improvements/'+improvement['id']
        verified=request('POST',path+'/verify',json=dict(expected_revision=improvement['revision'],
            baseline_job_id=jobs['baseline']['id'],candidate_job_id=jobs['candidate']['id'],
            note=a.review_note,acknowledge_unknown_usage=a.acknowledge_unpriced_usage))
        closed=request('POST',path+'/close',json=dict(expected_revision=verified['revision'],note=a.review_note))
        write('workflow-closure.json',dict(improvement=closed,reviewer='assistant_non_independent',quality_improvement_claim=False))
        print(json.dumps(dict(closed=closed['status']=='closed',quality_improvement_claim=False)))
    return 0

if __name__=='__main__':raise SystemExit(main())
