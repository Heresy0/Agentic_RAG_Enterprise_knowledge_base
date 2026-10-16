"""Three sequential authenticated live runs; explicitly opt in to model calls."""
import argparse
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--allow-model-calls',action='store_true')
    args=parser.parse_args()
    if not args.allow_model_calls:
        parser.error('Live ablation requires --allow-model-calls')
    from dotenv import dotenv_values
    from scripts.evaluate_system import main
    for name,k,parents in [('child-8',8,False),('child-12',12,False),('parent-12',12,True)]:
        token=(dotenv_values(ROOT/'.env.evaluation').get('ENTERPRISE_KB_ACCESS_TOKEN') or '').strip()
        os.environ['ENTERPRISE_KB_ACCESS_TOKEN']=token
        os.environ['RAG_RERANK_TOP_K']=str(k)
        os.environ['RAG_PARENT_CONTEXT_ENABLED']=str(parents).lower()
        print('Starting '+name,flush=True)
        result=main(['--mode','live','--config',str(ROOT/'evals/system_config.local.json'),
            '--dataset-dir',str(ROOT/'evals/current/groups/multihop'),
            '--corpus',str(ROOT/'output/knowledge-corpus-multihop'),
            '--layers','retrieval,rerank,context,answer','--allow-model-calls','--save-traces',
            '--output-dir',str(args.output_dir/name)])
        # A failed accuracy check is a valid experimental result, not a retry.
        print(f'Finished {name} exit={result}',flush=True)
