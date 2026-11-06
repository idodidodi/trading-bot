"""Local journal administration. Activation is a separate, explicit cutover step."""
import argparse
import json
import time
from pathlib import Path
from runtime_store import Store
from demo_execution import policy_valid


def main():
    p=argparse.ArgumentParser();p.add_argument('--database',required=True)
    p.add_argument('action',choices=['status','disable','configure-demo','activate'])
    p.add_argument('--policy',type=Path);p.add_argument('--reconciliation',type=Path)
    a=p.parse_args();store=Store(a.database)
    if a.action=='status':
        print(json.dumps(dict(enabled=bool(store.state()[0]),reconciled=bool(store.state()[1]),activated_at=store.state()[2])));return
    store.acquire_worker() # configuration cannot race the executor's broker cycle
    try:
        with store.connect() as db:
            if a.action=='disable':db.execute('UPDATE control SET enabled=0 WHERE id=1')
            elif a.action=='configure-demo':
                config=json.loads(a.policy.read_text());policy_valid(config)
                db.execute("INSERT INTO demo_execution_policy VALUES(1,1,0,0,?,'{}') ON CONFLICT(id) DO UPDATE SET config=excluded.config,enabled=0,revision=revision+1",(json.dumps(config),))
            else:
                review=json.loads(a.reconciliation.read_text())
                required=('old_owner_stopped','old_credentials_revoked','broker_orders_reconciled','positions_reconciled','notification_handoff_verified')
                if any(review.get(k) is not True for k in required) or not review.get('reviewer'):
                    raise ValueError('Complete signed-off reconciliation required')
                if db.execute("SELECT 1 FROM demo_execution_trades WHERE state NOT IN ('closed','unfilled','skipped','protected') LIMIT 1").fetchone():
                    raise ValueError('Resolve ambiguous demo intents before activation')
                if db.execute("SELECT 1 FROM paper_trades WHERE json_extract(payload,'$.state') IN ('submitting','submission unknown') LIMIT 1").fetchone():
                    raise ValueError('Resolve ambiguous paper intents before activation')
                now=int(time.time()*1000)
                db.execute('UPDATE control SET enabled=1,reconciled=1,activated_at=? WHERE id=1',(now,))
                db.execute('UPDATE demo_execution_policy SET enabled=1,activated_at=?,revision=revision+1 WHERE id=1',(now,))
                db.execute('INSERT INTO activity_log(occurred,event,details) VALUES(?,?,?)',(time.time(),'Cutover attestation',json.dumps(review)))
    finally:store.close()


if __name__=='__main__':main()
