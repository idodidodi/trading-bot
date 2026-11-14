"""Classify exported Market check logs offline; never contacts a provider/database.

Input: JSON array of coverage rows or activity-log rows with JSON `details`.
Output: counts and one timestamped example for each instrument/reason/classification.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from market_sessions import classify_unavailable


def audit(rows):
    counts, examples = Counter(), {}
    for event in rows:
        row = event.get('details', event)
        if isinstance(row, str):
            try:
                row = json.loads(row)
            except ValueError:
                continue
        if not isinstance(row, dict) or row.get('status') != 'unavailable':
            continue
        checked = row.get('request_at', row.get('checked_at'))
        if not isinstance(checked, (int, float)):
            continue
        assessment = classify_unavailable(row, checked, row.get('reason', ''))
        key = (row.get('provider'), row.get('symbol') or row.get('asset'),
               row.get('reason'), assessment['availability_classification'])
        counts[key] += 1
        examples.setdefault(key, dict(checked_at=checked, **assessment))
    return [dict(provider=k[0], symbol=k[1], reason=k[2], classification=k[3],
                 count=n, example=examples[k]) for k, n in counts.items()]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(json.loads(args.input.read_text())), indent=2))
