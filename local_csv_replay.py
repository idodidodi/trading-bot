"""Bounded pure replay subprocess. No scanner, database or provider calls."""
import json
import os
import sys


def main():
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    os.nice(10)
    from csv_backtests import run
    try:
        body = json.loads(sys.stdin.read(2_100_001))
        report = run(body)
        print(json.dumps({'report': report}))
    except (ValueError, TypeError, KeyError):
        print(json.dumps({'error': 'Invalid CSV replay input'}))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
