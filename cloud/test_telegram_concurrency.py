"""Run real concurrent claims against a disposable migrated PostgreSQL database."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
import threading
import uuid


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dsn', required=True)
    args=parser.parse_args()
    def sql(query):
        return subprocess.check_output(['psql',args.dsn,'-XAt','-v','ON_ERROR_STOP=1','-c',query],text=True).strip()
    owner=str(uuid.uuid4());inst=str(uuid.uuid4())
    sql(f"INSERT INTO auth.users VALUES('{owner}'); INSERT INTO dashboard_installations VALUES('{inst}','{owner}');")
    barrier=threading.Barrier(8)
    def claim(index):
        payload=dict(action='acquire',destination_hash='a'*64,event_hash='b'*64,body_hash='c'*64,
                     owner=str(uuid.uuid4()),sender_id=str(uuid.uuid4()),device=f'device-{index}',environment='dev' if index%2 else 'prod')
        barrier.wait(timeout=10)
        result=sql(f"SELECT telegram_coordinate('{inst}','{json.dumps(payload)}'::jsonb)->>'status'")
        return result
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(claim,range(8)))
        assert results.count('acquired')==1, results
        assert results.count('busy')==7, results
        print('Concurrent PostgreSQL claims passed: one acquired, seven busy.')
    finally:
        sql(f"DELETE FROM telegram_delivery_attempts WHERE installation='{inst}'; DELETE FROM telegram_delivery_claims WHERE installation='{inst}'; DELETE FROM dashboard_installations WHERE id='{inst}'; DELETE FROM auth.users WHERE id='{owner}';")


if __name__=='__main__':main()
