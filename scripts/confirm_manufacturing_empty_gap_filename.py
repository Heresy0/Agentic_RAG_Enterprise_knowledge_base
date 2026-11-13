"""Live 010/019 confirmation plus five fixed prior successes; no full100 score."""
import argparse
from datetime import datetime
from pathlib import Path

from scripts import manufacturing_gold_v4_grounded_live as transport

LIMITS = dict(max_model_calls=60, max_tokens=600000, max_seconds=1200, reservation_tokens=32000)
CASE_ORDER = (10, 19, 3, 12, 15, 36, 91)
CASE_IDS = {f'MFG-GOLD-{n:03}' for n in CASE_ORDER}


class ConfirmationBudget(transport.RunBudget):
    def __init__(self, state):
        super().__init__(state)
        self.limits = dict(LIMITS)
        self.deadline = datetime.fromisoformat(state['started_at']).timestamp() + LIMITS['max_seconds']


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('phase', choices=['case'])
    parser.add_argument('--case-id', required=True, choices=sorted(CASE_IDS))
    parser.add_argument('--bindings', type=Path, required=True)
    args, _ = parser.parse_known_args()
    bindings = transport.read(args.bindings)
    assert bindings['schema_version'] == 'manufacturing-empty-gap-filename-seven-v1'
    assert bindings['limits'] == LIMITS
    assert {row['case_id'] for row in bindings['cases']} == CASE_IDS
    assert all(row['platform'] == 'erp' for row in bindings['cases'])
    transport.RunBudget = ConfirmationBudget
    transport.main()


if __name__ == '__main__':
    main()
