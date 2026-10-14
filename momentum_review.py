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
    return dict(candidates=len(signals),outcome_states={state:sum(s.get('outcome',{}).get('status')==state for s in signals) for state in sorted({s.get('outcome',{}).get('status','unknown') for s in signals})},closed_trades=len(closed),wins=sum(x>0 for x in returns),
                win_rate_pct=100*sum(x>0 for x in returns)/len(returns) if returns else None,
                average_net_pct=sum(returns)/len(returns) if returns else None,
                profit_factor=gains/losses if losses else None,
                max_adverse_pct=max((s.get('adverse_pct',0) for s in closed),default=None))


def group_metrics(signals, key):
    groups={}
    for signal in signals:
        value=signal.get(key) or 'unknown'
        groups.setdefault(value,[]).append(signal)
    return {value:metrics(rows) for value,rows in sorted(groups.items())}


def paper_metrics(trades):
    result={}
    for trade in trades:
        signal=trade.get('signal',{});market=signal.get('market','unknown');version=signal.get('version','unknown')
        group=result.setdefault(f'{market}/{version}',dict(states={},closed_fills=0,gross_returns_pct=[]))
        state=trade.get('state','unknown');group['states'][state]=group['states'].get(state,0)+1
        if 'gross_return_pct' in trade:
            group['closed_fills']+=1;group['gross_returns_pct'].append(trade['gross_return_pct'])
    for group in result.values():
        returns=group.pop('gross_returns_pct')
        group['average_gross_pct']=sum(returns)/len(returns) if returns else None
    return result


def review(store):
    live=view(store,limit=None);backtest=view(store,'backtest')
    from paper_trading import view as paper_view
    paper=paper_view(store)
    with store.connect() as db:
        initialize(db)
        row=db.execute('SELECT payload FROM momentum_reports ORDER BY id DESC LIMIT 1').fetchone()
    replay=json.loads(row[0]) if row else backtest
    replay_signals=replay.get('signals',[])
    report=dict(paper_execution=paper,reviewed_at=int(time.time()*1000),version=VERSION,
                live={},by_version={},replay=backtest.get('metrics',{}),replay_by_market=group_metrics(replay_signals,'market'),
                replay_by_version={market:group_metrics([s for s in replay_signals if s.get('market')==market],'version') for market in sorted({s.get('market','unknown') for s in replay_signals})},coverage=live.get('latest',{}),
                decision='Retain current rules until sufficient forward results and independent holdout evidence support a change.',
                improvement_policy='Review failures and entry gaps first. Propose small changes on a training window, validate on a later untouched time window with costs, compare by market and version. At least 30 completed forward trades per market before tuning from live outcomes. Three days alone cannot establish a statistical edge. Never rewrite issued signal rules or levels. Version and test accepted changes.')
    report['paper_return_note']='Actual paper fills; gross returns exclude fees. Separate from daily candle simulation and backtests.'
    report['paper_returns']={m:{'closed_fills':len(v),'average_gross_pct':sum(v)/len(v) if v else None} for m in ('stock','crypto') for v in [[r['gross_return_pct'] for r in paper['trades'] if r['signal']['market']==m and 'gross_return_pct' in r]]}
    report['paper_by_market_version']=paper_metrics(paper['trades'])
    report['paper_by_market']={m:{state:sum(r['signal']['market']==m and r['state']==state for r in paper['trades']) for state in sorted({r['state'] for r in paper['trades']})} for m in ('stock','crypto')}
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
