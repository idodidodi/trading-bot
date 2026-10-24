#!/usr/bin/env python3
"""Read-only pre-install check. Does not import the application or print a DSN."""
import argparse
import os
from pathlib import Path
import statistics
import sys
import time


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file',type=Path,required=True,help='Separate new-install environment file; never executed as shell')
    args=parser.parse_args()
    try:
        for line in args.env_file.read_text().splitlines():
            if line.strip().startswith('TRADING_DATABASE_URL='):
                os.environ['TRADING_DATABASE_URL']=line.split('=',1)[1].strip().strip('\"\'')
        import psycopg
        from psycopg.conninfo import conninfo_to_dict
        dsn=os.environ.get('TRADING_DATABASE_URL','')
        options=conninfo_to_dict(dsn)
        if not dsn or options.get('sslmode') not in ('verify-full','verify-ca'):
            raise ValueError('Configure TRADING_DATABASE_URL with sslmode=verify-full and a trusted CA')
        started=time.monotonic()
        with psycopg.connect(dsn,connect_timeout=8,autocommit=True,prepare_threshold=None) as db:
            connect_ms=(time.monotonic()-started)*1000
            ssl=db.execute('SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()').fetchone()
            if ssl!=(True,):raise ValueError('Server did not confirm TLS')
            db.execute('BEGIN READ ONLY')
            latency=[]
            for _ in range(5):
                before=time.monotonic();db.execute('SELECT 1').fetchone();latency.append((time.monotonic()-before)*1000)
            db.execute('ROLLBACK')
            print(f'Connected with TLS; handshake {connect_ms:.0f} ms; SELECT 1 median {statistics.median(latency):.0f} ms, max {max(latency):.0f} ms (5 probes).')
            print('Use the Supabase shared SESSION pooler IPv4 host/port from Connect; this check makes no schema changes.')
        return 0
    except ImportError:
        print('Install psycopg[binary] in the preflight virtual environment first.',file=sys.stderr)
    except Exception as exc:
        # Exception text/tracebacks may contain DSN, password, hostname or env file.
        code=getattr(exc,'sqlstate',None)
        print(f'Connection check failed ({type(exc).__name__}'+(f', SQLSTATE {code}' if code else '')+'). Check DNS/IPv4, session-pooler username/password, TLS CA, firewall and project availability.',file=sys.stderr)
    return 1


if __name__=='__main__':sys.exit(main())
