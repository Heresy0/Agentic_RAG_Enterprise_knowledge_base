"""One public CLI for quality, load, saved Ragas and paired comparisons."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
COMMANDS={
    'system':'evaluate_system.py',
    'load':'run_load_test.py',
    'ragas':'evaluate_saved_ragas.py',
    'ragas-compare':'compare_saved_ragas.py',
    'multihop-compare':'evaluate_multihop_comparison.py',
    'check':'build_evaluation_suite.py',
}


def main(argv=None):
    args=list(sys.argv[1:] if argv is None else argv)
    p=argparse.ArgumentParser(description=__doc__,epilog='Use COMMAND --help for its options. No command defaults to system.')
    p.add_argument('command',nargs='?',choices=[*COMMANDS,'catalog'],default='system')
    if args and args[0] in ('-h','--help'):p.print_help();return 0
    if not args or args[0] not in (*COMMANDS,'catalog'):args.insert(0,'system')
    command,remaining=args[0],args[1:]
    if command=='catalog':
        if remaining:p.error('catalog has no options')
        print((ROOT/'evals/current/suite.json').read_text(encoding='utf-8'));return 0
    if command=='check' and '--check' not in remaining:remaining=['--check',*remaining]
    return subprocess.run([sys.executable,str(ROOT/'scripts'/COMMANDS[command]),*remaining],cwd=ROOT).returncode


if __name__=='__main__':raise SystemExit(main())
