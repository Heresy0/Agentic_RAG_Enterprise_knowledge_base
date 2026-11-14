"""Two-case field-refresh protocol check, separate from every full100 score."""
import argparse
from datetime import datetime
from pathlib import Path

from scripts import manufacturing_gold_v4_grounded_live as transport

LIMITS = dict(max_model_calls=30, max_tokens=300000, max_seconds=600, reservation_tokens=32000)
CASE_IDS = {"MFG-GOLD-015", "MFG-GOLD-024"}


class ProtocolBudget(transport.RunBudget):
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
    assert bindings['schema_version'] == 'manufacturing-field-refresh-protocol-v1'
    assert bindings['limits'] == LIMITS
    assert {row['case_id'] for row in bindings['cases']} <= CASE_IDS
    assert args.case_id in {row['case_id'] for row in bindings['cases']}
    # Only this standalone process uses the smaller budget; historical entries stay frozen.
    transport.RunBudget = ProtocolBudget
    transport.main()


if __name__ == '__main__':
    main()
