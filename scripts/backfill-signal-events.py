"""Explicit historical backfill. Every emitted event is permanently replay-only."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from platform_app import Store
from signal_service import publish


def main():
    store=Store()
    # No load_env, scanner, broker or Telegram process is started.
    for table,strategy in [('signals','rsi'),('rsi_pb_signals','rsi-pb'),('pin_bar_signals','pin-bars'),('bb_pin_bar_signals','bb-pin-bars')]:
        after=''
        while True:
            with store.connect() as db:
                rows=db.execute('SELECT id,payload FROM '+table+' WHERE id>%s ORDER BY id LIMIT 100',(after,)).fetchall()
                for key,raw in rows:publish(db,strategy,json.loads(raw),origin='replay')
            if not rows:break
            after=rows[-1][0]
    print('Historical findings exported as replay-only events.')


if __name__=='__main__':main()
