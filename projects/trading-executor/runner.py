"""Independent subscriber process. Disabled execution still durably consumes."""
import argparse
import json
import os
import time
from runtime_store import Store,assert_worker
from signal_client import Client


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--database',required=True)
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    store=Store(args.database);store.acquire_worker()
    client=Client(os.environ['SIGNAL_API_URL'],os.environ['SIGNAL_PROJECT_TOKEN'],os.environ.get('SIGNAL_PROJECT','trading-executor'))
    try:
        while True:
            page=client.poll(store)
            # Catch up the complete stream before acting; a later page can invalidate.
            if not page['has_more'] and os.environ.get('EXECUTION_ENABLED')=='true':
                assert_worker(store)
                import demo_execution,paper_trading
                demo_execution.check(store)
                if os.environ.get('PAPER_TRADING_ENABLED')=='true':paper_trading.check_and_log(store)
            client.flush_notifications(store)
            if args.once:break
            if not page['has_more']:time.sleep(15)
    finally:store.close()


if __name__=='__main__':main()
