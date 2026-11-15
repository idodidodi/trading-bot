#!/usr/bin/env python3
"""One-time, read-only hosted candle export into an EMPTY local working store.
Run with scanner stopped. No provider/Telegram modules or effects are invoked.
"""
import argparse
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import json
import psycopg
from working_postgres import configured
from local_cache import TABLES


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--writers-stopped',action='store_true')
    args=parser.parse_args()
    if not args.writers_stopped: parser.error('Stop the existing scanner before seeding; pass --writers-stopped after verification')
    local=configured()
    if not local: parser.error('TRADING_WORKING_DATABASE_URL is required')
    totals={}
    with psycopg.connect(os.environ['TRADING_DATABASE_URL'],connect_timeout=10) as source, local.connect() as target:
        source.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        target.execute('SET LOCAL statement_timeout=0')
        target.execute('SELECT pg_advisory_xact_lock(714269101)')
        if target.execute('SELECT count(*) FROM candles WHERE namespace=%s',(local.namespace,)).fetchone()[0]:
            raise ValueError('Destination namespace must be empty; refuse overwriting working history')
        for table,(columns,width,frame,start) in TABLES.items():
            count=0
            with source.cursor(name='seed_'+table) as cursor:
                cursor.execute(f'SELECT {columns} FROM bot_runtime.{table} ORDER BY feed,timeframe,start')
                for row in cursor:
                    target.execute('INSERT INTO candles VALUES(%s,%s,%s,%s,%s,%s::jsonb)',
                        (local.namespace,table,row[0],row[frame],row[start],json.dumps(row,allow_nan=False)))
                    count+=1
            actual=target.execute('SELECT count(*) FROM candles WHERE namespace=%s AND kind=%s',(local.namespace,table)).fetchone()[0]
            if actual!=count:raise ValueError('Imported row count mismatch')
            totals[table]=count
    print(json.dumps({'imported':totals,'hosted_modified':False}))

if __name__=='__main__':
    try:main()
    except Exception:
        print('Import failed; local transaction rolled back. Inspect connectivity/schema; credentials suppressed.',file=sys.stderr)
        sys.exit(1)
