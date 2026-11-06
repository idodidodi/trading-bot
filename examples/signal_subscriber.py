"""Minimal generic subscriber example: journal signals, never trade or send alerts."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import urllib.request


def main():
    p=argparse.ArgumentParser();p.add_argument('--database',required=True);p.add_argument('--once',action='store_true');a=p.parse_args()
    base=os.environ['SIGNAL_API_URL'].rstrip('/');token=os.environ['SIGNAL_PROJECT_TOKEN']
    def request(path,body=None):
        req=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,
            headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=10) as response:return json.load(response)
    db=sqlite3.connect(a.database)
    try:
        db.execute('CREATE TABLE IF NOT EXISTS inbox(event_id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS progress(id INTEGER PRIMARY KEY,cursor INTEGER NOT NULL)')
        row=db.execute('SELECT cursor FROM progress WHERE id=1').fetchone()
        page=request('/v1/events'+('?after='+str(row[0]) if row else ''))
        if page['project']!=os.environ['SIGNAL_PROJECT']:raise ValueError('Wrong subscriber project')
        with db:
            for event in page['events']:db.execute('INSERT OR IGNORE INTO inbox VALUES(?,?)',(event['event_id'],json.dumps(event)))
            db.execute('INSERT INTO progress VALUES(1,?) ON CONFLICT(id) DO UPDATE SET cursor=excluded.cursor',(page['next_cursor'],))
        request('/v1/ack',dict(cursor=page['next_cursor']))
    finally:db.close()


if __name__=='__main__':main()
