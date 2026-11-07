"""Validate reviewed calendar JSON; --apply requires an explicit administrator DSN."""
import argparse
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from signal_discovery import calendar


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file',type=Path)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    values=json.loads(args.file.read_text())
    if not isinstance(values,dict) or not values or len(values)>1000:parser.error('Expected source-to-calendar mapping, maximum 1000 sources')
    for source,value in values.items():
        if not isinstance(source,str) or not 1<=len(source)<=200:parser.error('Invalid source identity')
        calendar(value)
    if args.apply:
        import psycopg
        dsn=os.environ.get('DISCOVERY_ADMIN_DATABASE_URL')
        if not dsn:parser.error('Explicit DISCOVERY_ADMIN_DATABASE_URL required')
        with psycopg.connect(dsn) as db:
            for source,value in values.items():
                db.execute('INSERT INTO bot_runtime.signal_discovery_calendars VALUES(%s,%s) ON CONFLICT(source) DO UPDATE SET payload=excluded.payload',(source,json.dumps(value)))
    print(json.dumps(dict(validated=len(values),applied=args.apply)))


if __name__=='__main__':main()
