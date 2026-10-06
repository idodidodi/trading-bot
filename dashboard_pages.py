"""Local dashboard views with editable finding feedback."""
import html
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from platform_app import ROOT
from finding_feedback import latest_report, feedback_map, cell, STYLE, SCRIPT

LOCAL_TIMEZONE = ZoneInfo('Asia/Jerusalem')


def table(headers, rows):
    escape = lambda v: html.escape(str(v))
    return '<table><tr>' + ''.join('<th>'+escape(h)+'</th>' for h in headers) + '</tr>' + ''.join('<tr>'+''.join('<td>'+escape(v)+'</td>' for v in row)+'</tr>' for row in rows) + '</table>'


def render_page(store, path):
    with store.connect() as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if path == '/signals':
            title = 'Signals'
            feedback = feedback_map(db)
            rows = ''
            for raw, status, attempts, error in store.rows():
                p = json.loads(raw)
                values = (p['symbol'], p['timeframe'], p['direction'] + (' (provisional)' if p.get('signal_status') == 'provisional' else ''), f"{p['price1']:g} → {p['price2']:g}",
                          f"{p['rsi1']:.2f} → {p['rsi2']:.2f}", status, attempts, error or '')
                from candle_views import link
                rows += '<tr>' + ''.join(f'<td>{html.escape(str(v))}</td>' for v in values) + '<td>' + link('live', p) + '</td>' + cell('live', p, feedback) + '</tr>'
            content = '<table><tr><th>Asset</th><th>Timeframe</th><th>Signal</th><th>Price pivots</th><th>RSI pivots</th><th>Delivery</th><th>Attempts</th><th>Error</th><th>Candle</th><th>Feedback</th></tr>' + rows + '</table>'
            if not rows:
                content += '<p>No signals received yet.</p>'
            content += '<p>Last 100 received signals. No rows means no signals received; it does not establish market coverage.</p><p><a href="/api/feedback/export">Export feedback history</a> · Saved in local SQLite. Online storage not configured.</p>'
        elif path == '/logs':
            title = 'Logs'
            rows = db.execute('SELECT occurred,event,details FROM activity_log ORDER BY id DESC LIMIT 200').fetchall() if 'activity_log' in tables else []
            content = table(['Time (Israel local)', 'Event', 'Details'], [(datetime.fromtimestamp(t, LOCAL_TIMEZONE).isoformat(timespec='seconds'), e, d) for t,e,d in rows]) if rows else '<p>No activity recorded yet. Restart the updated app to begin logging.</p>'
        else:
            title = 'Backtest 2020'
            report = latest_report(db, ROOT)
            content = '<p>Run <code>python3 backtest.py</code> with historical CSV candles, including pre-2020 warm-up. Only signals confirmed in 2020 appear. Historical replay does not send Telegram alerts.</p>'
            if not report:
                content += '<p>No backtest run recorded yet.</p>'
            else:
                content += table(['Asset','Timeframe','Status','Candles','Signals','Details'], [(r['asset'],r['timeframe'],r['status'],r.get('candles',0),len(r['signals']),r.get('reason',r.get('note',''))) for r in report['results']])
                feedback = feedback_map(db)
                headers = ['Asset','Timeframe','Direction','Confirmed (Israel local)','Price 1','Price 2','RSI 1','RSI 2','Candle','Feedback']
                content += '<table><tr>' + ''.join('<th>'+html.escape(h)+'</th>' for h in headers) + '</tr>'
                for result in report['results']:
                    for signal in result['signals']:
                        values = (result['asset'],result['timeframe'],signal['direction'],datetime.fromtimestamp(signal['confirmed_at']/1000,LOCAL_TIMEZONE).strftime('%d %b %Y, %H:%M %Z'),signal['price1'],signal['price2'],round(signal['rsi1'],2),round(signal['rsi2'],2))
                        from candle_views import link
                        content += '<tr>' + ''.join('<td>'+html.escape(str(v))+'</td>' for v in values) + '<td>' + link('backtest', signal) + '</td>' + cell('backtest', signal, feedback) + '</tr>'
                content += '</table><p><a href="/api/feedback/export">Export feedback history</a> · Saved in local SQLite. Online storage not configured.</p>'
    return '<!doctype html><html><head><meta charset="utf-8"><title>'+title+'</title><style>body{font:16px system-ui;background:#111827;color:#e5e7eb;margin:40px}a{color:#67e8f9}table{border-collapse:collapse;width:100%}td,th{padding:12px;text-align:left;border-bottom:1px solid #374151}</style>'+STYLE+'</head><body><nav><a href="/">Overview</a> · <a href="/signals">Signals</a> · <a href="/logs">Logs</a> · <a href="/backtest">Backtest 2020</a> · <a href="/assets">Assets</a></nav><h1>'+title+'</h1>'+content+SCRIPT+'</body></html>'
