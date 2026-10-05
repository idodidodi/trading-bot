"""Read-only local dashboard views."""
import html
import json
from datetime import datetime, timezone


def table(headers, rows):
    escape = lambda v: html.escape(str(v))
    return '<table><tr>' + ''.join('<th>'+escape(h)+'</th>' for h in headers) + '</tr>' + ''.join('<tr>'+''.join('<td>'+escape(v)+'</td>' for v in row)+'</tr>' for row in rows) + '</table>'


def render_page(store, path):
    with store.connect() as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if path == '/logs':
            title = 'Logs'
            rows = db.execute('SELECT occurred,event,details FROM activity_log ORDER BY id DESC LIMIT 200').fetchall() if 'activity_log' in tables else []
            content = table(['Time (UTC)', 'Event', 'Details'], [(datetime.fromtimestamp(t, timezone.utc).isoformat(), e, d) for t,e,d in rows]) if rows else '<p>No activity recorded yet. Restart the updated app to begin logging.</p>'
        else:
            title = 'Backtest 2020'
            row = db.execute('SELECT report FROM backtest_runs ORDER BY id DESC LIMIT 1').fetchone() if 'backtest_runs' in tables else None
            content = '<p>Run <code>python3 backtest.py</code> with historical CSV candles, including pre-2020 warm-up. Only signals confirmed in 2020 appear. Historical replay does not send Telegram alerts.</p>'
            if not row:
                content += '<p>No backtest run recorded yet.</p>'
            else:
                report = json.loads(row[0])
                content += table(['Asset','Timeframe','Status','Candles','Signals','Details'], [(r['asset'],r['timeframe'],r['status'],r.get('candles',0),len(r['signals']),r.get('reason',r.get('note',''))) for r in report['results']])
                signals = [(r['asset'],r['timeframe'],s['direction'],datetime.fromtimestamp(s['confirmed_at']/1000,timezone.utc).isoformat(),s['price1'],s['price2'],round(s['rsi1'],2),round(s['rsi2'],2)) for r in report['results'] for s in r['signals']]
                content += table(['Asset','Timeframe','Direction','Confirmed (UTC)','Price 1','Price 2','RSI 1','RSI 2'], signals)
    return '<!doctype html><html><head><meta charset="utf-8"><title>'+title+'</title><style>body{font:16px system-ui;background:#111827;color:#e5e7eb;margin:40px}a{color:#67e8f9}table{border-collapse:collapse;width:100%}td,th{padding:12px;text-align:left;border-bottom:1px solid #374151}</style></head><body><nav><a href="/">Overview</a> · <a href="/logs">Logs</a> · <a href="/backtest">Backtest 2020</a></nav><h1>'+title+'</h1>'+content+'</body></html>'
