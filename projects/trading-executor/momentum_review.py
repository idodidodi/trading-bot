"""Persist an evidence review without silently optimizing or rewriting issued trades."""
import json
import os
import time
from pathlib import Path

from runtime_store import Store


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
    from paper_trading import view
    paper=view(store)
    report=dict(reviewed_at=int(time.time()*1000),paper_execution=paper,metrics=paper_metrics(paper['trades']))
    with store.connect() as db:db.execute('INSERT INTO momentum_reviews(payload) VALUES(?)',(json.dumps(report),))
    return report


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--database',required=True);args=parser.parse_args()
    print(json.dumps(review(Store(args.database)),indent=2))
