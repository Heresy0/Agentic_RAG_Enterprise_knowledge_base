"""Configure the existing offline/paid comparison runner for isolated prompt v2."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import compare_manufacturing_prompt_candidate as experiment

CANDIDATE=ROOT/'evals/manufacturing_prompt_candidate_v2/candidate-system.txt'


def freeze(directory):
    experiment.CANDIDATE=CANDIDATE
    experiment.freeze(directory)
    plan=experiment.read(directory/'plan.json')
    prior=ROOT/'evals/manufacturing_prompt_candidate_v1'
    for p in [Path(__file__),ROOT/'tests/test_prompt_candidate_v2.py',prior/'candidate-system.txt',
              ROOT/'scripts/measure_manufacturing_generation_stability.py']:
        plan['protected'][p.relative_to(ROOT).as_posix()]=experiment.digest(p)
    for p in (prior/'runs/20261114-r16').rglob('*'):
        if p.is_file():plan['protected'][p.relative_to(ROOT).as_posix()]=experiment.digest(p)
    plan.update(candidate_version='v2',
        authorization='用户明确批准第二版独立候选与现有模型同批对照，固定上下文、动态要求、Schema及校验，不替换线上、不加题号规则。',
        scope='Only ordinary document system prompt replaced with v2; current production prompt is control; dynamic contract preserved.')
    (directory/'plan.json').write_bytes(json.dumps(plan,ensure_ascii=False,indent=2).encode('utf-8'))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('freeze','run','finish'))
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--credentials',type=Path)
    args=parser.parse_args();directory=args.run_dir.resolve();directory.relative_to(ROOT/'tmp')
    if args.mode=='freeze':freeze(directory)
    elif args.mode=='run':
        assert experiment.read(directory/'plan.json')['candidate_version']=='v2'
        experiment.run(directory,args.credentials.resolve())
    else:experiment.finish(directory)
