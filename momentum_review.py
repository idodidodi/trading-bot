"""Persist an evidence review without silently optimizing or rewriting issued trades."""
import json
import os
import time
from pathlib import Path
from momentum import view,initialize,VERSION
from platform_app import ROOT,Store,load_env


def metrics(signals):
    closed=[s['outcome'] for s in signals if 'net_pct' in s.get('outcome',{})]
    returns=[s['net_pct'] for s in closed];gains=sum(x for x in returns if x>0);losses=-sum(x for x in returns if x<0)
    return dict(closed_trades=len(closed),wins=sum(x>0 for x in returns),
                win_rate_pct=100*sum(x>0 for x in returns)/len(returns) if returns else None,
                average_net_pct=sum(returns)/len(returns) if returns else None,
                profit_factor=gains/losses if losses else None,
                max_adverse_pct=max((s.get('adverse_pct',0) for s in closed),default=None))


def review(store):
    live=view(store,limit=None);backtest=view(store,'backtest')
    report=dict(reviewed_at=int(time.time()*1000),version=VERSION,
                live={},by_version={},replay=backtest.get('metrics',{}),coverage=live.get('latest',{}),
                decision='Retain current rules until sufficient forward results and independent holdout evidence support a change.',
                improvement_policy='Review failures and entry gaps first. Propose small changes on a training window, validate on a later untouched time window with costs, compare by market and version. At least 30 completed forward trades per market before tuning from live outcomes. Three days alone cannot establish a statistical edge. Never rewrite issued signal rules or levels. Version and test accepted changes.')
    for market in ('stock','crypto'):
        signals=[s for s in live['signals'] if s['market']==market]
        report['live'][market]=metrics(signals)
        report['by_version'][market]={version:metrics([s for s in signals if s['version']==version]) for version in sorted({s['version'] for s in signals})}
    with store.connect() as db:
        initialize(db)
        db.execute('CREATE TABLE IF NOT EXISTS momentum_reviews(id INTEGER PRIMARY KEY,payload TEXT NOT NULL)')
        db.execute('INSERT INTO momentum_reviews(payload) VALUES(?)',(json.dumps(report),))
    folder=Path(os.environ.get('DATA_DIR',str(ROOT/'data')))/'momentum-reviews';folder.mkdir(parents=True,exist_ok=True)
    (folder/f"review-{report['reviewed_at']}.json").write_text(json.dumps(report,indent=2))
    store.log('Momentum strategy review',json.dumps(dict(version=VERSION,live=report['live'],decision=report['decision'])))
    return report


if __name__=='__main__':
    load_env();store=Store(Path(os.environ.get('DATA_DIR',str(ROOT/'data')))/'signals.sqlite3')
    print(json.dumps(review(store),indent=2))
