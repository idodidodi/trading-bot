"""RSI candle-close divergence followed by exactly one opposite shape-only pinbar."""
from dataclasses import asdict
from datetime import datetime, timedelta, time, timezone
import hashlib
import json
from zoneinfo import ZoneInfo

STRATEGY = 'rsi-pb'
TABLE_PREFIX = 'rsi_pb'
COVERAGE_KEY = 'rsi_pb'


def load_rules(rsi=None):
    from platform_app import load_rules as rsi_rules
    from pin_bars import load_rules as pin_rules
    pin = pin_rules()
    rules = dict(rsi=rsi or rsi_rules(), ratio=pin['ratio'], max_short_wick_ratio=pin['max_short_wick_ratio'],
                 seed='RSI candle-close divergence; next candle only; opposite wick shape; no EMA filters')
    rules['version'] = 'rsi-pb-v1-' + hashlib.sha256(json.dumps(rules, sort_keys=True).encode()).hexdigest()[:16]
    return rules


def minimum_history(rules):
    from scanner import minimum_history as rsi_minimum
    return rsi_minimum(rules['rsi'])


def direction(bar, ratio=2, max_short_wick_ratio=.66):
    body = abs(bar.open-bar.close)
    lower, upper = min(bar.open,bar.close)-bar.low, bar.high-max(bar.open,bar.close)
    if lower > ratio*body and lower > upper and upper <= lower*max_short_wick_ratio:
        return 'bullish'
    if upper > ratio*body and upper > lower and lower <= upper*max_short_wick_ratio:
        return 'bearish'


def next_start(bar, timeframe, asset=None):
    """Preserve native alignment; skip verified market closures, never missing sessions."""
    from scanner import interval_end
    from market_calendar import is_stock, is_forex, stock_trading_day, stock_close, NY, forex_market_open, next_forex_open
    asset = asset or {}
    stock, forex = is_stock(asset), is_forex(asset)
    if asset.get('symbol') == '^GSPC':
        stock = True
    dated = asset.get('provider') in ('fmp','yahoo') and timeframe in ('daily','weekly','monthly')
    zone = NY if stock or forex else timezone.utc
    if dated or bar.start % 86400000 == 0:
        zone = timezone.utc
    start = datetime.fromtimestamp(bar.start/1000, zone)
    expected = interval_end(start, timeframe)
    if timeframe in ('1h','4h'):
        sessions = asset.get('_rsi_pb_sessions',[])
        for opening,closing in sessions:
            if opening <= bar.start < closing and bar.end < closing:
                return bar.end
            if opening >= bar.end:
                return opening
        expected = datetime.fromtimestamp(bar.end/1000, zone)
        if stock and expected >= stock_close(start.date()):
            day = start.date()+timedelta(days=1)
            while not stock_trading_day(day):
                day += timedelta(days=1)
            # Alpaca buckets start at the session open.
            expected = datetime.combine(day,time(9,30),NY)
        elif forex and not forex_market_open(int(expected.timestamp()*1000)):
            expected = datetime.fromtimestamp(next_forex_open(int(expected.timestamp()*1000))/1000,zone)
    elif timeframe == 'daily':
        if stock:
            while not stock_trading_day(expected.date()):
                expected += timedelta(days=1)
        elif forex:
            # Daily forex bars may start at midnight UTC or the New York rollover.
            while not (forex_market_open(int(expected.timestamp()*1000)) or
                       next_forex_open(int(expected.timestamp()*1000)) < int((expected+timedelta(days=1)).timestamp()*1000)):
                expected += timedelta(days=1)
        elif dated and asset.get('market') in ('index','indices'):
            while expected.weekday()>=5:
                expected += timedelta(days=1)
    return int(expected.timestamp()*1000)


def setups(candles, rules, source, symbol, timeframe):
    from scanner import detect as rsi_detect
    grouped = {}
    for signal in rsi_detect(candles,rules['rsi'],source,timeframe,provisional=True):
        key = (signal['pivot2'],signal['direction'])
        grouped.setdefault(key,[]).append(signal)
    indexed = {b.start:b for b in candles}
    return [dict(strategy=STRATEGY,version=rules['version'],source=source,symbol=symbol,timeframe=timeframe,
                 direction=bias,divergences=refs,pivot2=pivot,pivot_closed_at=indexed[pivot].end,
                 pivot_candle=asdict(indexed[pivot]),rules=rules)
            for (pivot,bias),refs in grouped.items()]


def qualify(setup, bar):
    rules = setup['rules']
    bias = direction(bar,rules['ratio'],rules['max_short_wick_ratio'])
    if not bias or bias == setup['direction']:
        return None
    return setup | dict(pinbar_direction=bias,candle_at=bar.start,confirmed_at=bar.end,
                        open=bar.open,high=bar.high,low=bar.low,close=bar.close,
                        body=abs(bar.open-bar.close),lower_wick=min(bar.open,bar.close)-bar.low,
                        upper_wick=bar.high-max(bar.open,bar.close))


def detect(candles, rules, source, symbol, timeframe, asset=None):
    """Replay with no future pivot-confirmation requirement or extra-candle search."""
    from scanner import Candle
    indexed = {b.start:b for b in candles}
    signals = []
    for setup in setups(candles,rules,source,symbol,timeframe):
        following = indexed.get(next_start(Candle(**setup['pivot_candle']),timeframe,asset))
        if following:
            signal = qualify(setup,following)
            if signal:
                signals.append(signal)
    return sorted(signals,key=lambda s:(s['confirmed_at'],s['pivot2'],s['direction']))


def initialize(db):
    from candle_strategy import initialize as init
    import sys
    init(db,sys.modules[__name__])
    db.execute('CREATE TABLE IF NOT EXISTS rsi_pb_pending(id TEXT PRIMARY KEY,feed TEXT,timeframe TEXT,payload TEXT NOT NULL)')


def identity(source, timeframe, setup):
    return hashlib.sha256(json.dumps([source,timeframe,setup['version'],setup['pivot2'],setup['direction']]).encode()).hexdigest()


def scan(store, asset, timeframe, candles, rules, now, selection_start=None, catchup=False):
    from scanner import check_candles, Candle
    from scanner_control import initialize as init_notifications
    from telegram_routing import remember
    candles = check_candles(candles,now)
    if len(candles) < minimum_history(rules):
        return dict(status='insufficient history',candles=len(candles),reason=f'Need {minimum_history(rules)} closed candles')
    if asset.get('continuous') and any(a.end != b.start for a,b in zip(candles,candles[1:])):
        return dict(status='unavailable',reason='Missing candles in continuous-market feed')
    age = max(0,(now-candles[-1].end)//1000)
    if asset.get('max_data_age_seconds') and age > asset['max_data_age_seconds']:
        return dict(status='unavailable',reason='Latest closed candle is older than the configured freshness limit')
    source = f"{asset['provider']}:{asset.get('exchange') or 'configured'}:{asset.get('symbol') or asset['id']}"
    feed = f"{asset['id']}@{source}@{rules['version']}"
    candidates = [] if asset.get('rsi_pb_followup_only') else setups(candles,rules,source,asset['id'],timeframe)
    indexed = {b.start:b for b in candles}
    new, missing = 0, False
    with store.connect() as db:
        initialize(db)
        init_notifications(db)
        db.execute('BEGIN IMMEDIATE')
        state = db.execute('SELECT last_end FROM rsi_pb_state WHERE feed=? AND timeframe=?',(feed,timeframe)).fetchone()
        cutoff = state[0] if state else candles[-1].end
        if state and cutoff < candles[0].start:
            return dict(status='unavailable',reason='Polling gap exceeds available history; supply candle backfill')
        for setup in candidates:
            if setup['pivot_closed_at'] < (selection_start or 0):
                continue
            if setup['pivot_closed_at'] > cutoff or (not state and setup['pivot_closed_at']==cutoff):
                setup.update(asset_config={k:v for k,v in asset.items() if k!='_rsi_pb_sessions'},market=asset.get('market') or 'unknown')
                key = identity(feed,timeframe,setup)
                db.execute('INSERT OR IGNORE INTO rsi_pb_pending VALUES(?,?,?,?)',(key,feed,timeframe,json.dumps(setup)))
        for key,raw in db.execute('SELECT id,payload FROM rsi_pb_pending WHERE feed=? AND timeframe=?',(feed,timeframe)).fetchall():
            setup = json.loads(raw)
            expected = next_start(Candle(**setup['pivot_candle']),timeframe,asset)
            bar = indexed.get(expected)
            if bar is None:
                missing |= candles[-1].start > expected
                continue
            signal = qualify(setup,bar)
            if signal:
                signal.pop('asset_config',None)
                signal.update(detected_at=now)
                inserted = db.execute('INSERT OR IGNORE INTO rsi_pb_signals VALUES(?,?,?)',(key,bar.end,json.dumps(signal))).rowcount
                new += inserted
                if inserted and not catchup:
                    remember(db,'notification:rsi-pb:'+key,asset.get('market') or 'unknown',STRATEGY)
                    db.execute('INSERT OR IGNORE INTO notification_queue(id,text) VALUES(?,?)',('rsi-pb:'+key,message(signal)))
            db.execute('DELETE FROM rsi_pb_pending WHERE id=?',(key,))
        waiting = db.execute('SELECT COUNT(*) FROM rsi_pb_pending WHERE feed=? AND timeframe=?',(feed,timeframe)).fetchone()[0]
        db.execute('INSERT INTO rsi_pb_state VALUES(?,?,?) ON CONFLICT(feed,timeframe) DO UPDATE SET last_end=MAX(last_end,excluded.last_end)',(feed,timeframe,candles[-1].end))
    return dict(status='unavailable' if missing else 'scanned' if state else 'baseline set',candles=len(candles),
                reason='Missing follow-up candle; supply backfill' if missing else '',waiting_setups=waiting,
                last_closed_at=candles[-1].end,new_signals=new,limited_history=len(candles)<250,last_closed_age_seconds=age)


def retain_pending(store, config, configured, version=None):
    """Retain automatic selections for one follow-up, honoring explicit owner removals."""
    from managed_assets import for_timeframe, SCAN_STRATEGIES
    by_id = {a['id']:a for a in config['assets']}
    owner = {a['id']:a for a in configured['assets']}
    version = version or load_rules()['version']
    with store.connect() as db:
        initialize(db)
        for key,raw in db.execute('SELECT id,payload FROM rsi_pb_pending').fetchall():
            setup = json.loads(raw)
            old,frame = setup['asset_config'],setup['timeframe']
            current = owner.get(old['id'])
            automatic = old.get('daily_selected') or old.get('weekly_screen_selected')
            allowed = current or (old if automatic else None)
            if (not allowed or not allowed.get('enabled',True) or frame not in allowed.get('timeframes',configured['timeframes'])
                    or STRATEGY not in allowed.get('scan_strategies',SCAN_STRATEGIES) or setup['version'] != version):
                db.execute('DELETE FROM rsi_pb_pending WHERE id=?',(key,))
                continue
            if current and source_for(for_timeframe(current,frame)) != setup['source']:
                db.execute('DELETE FROM rsi_pb_pending WHERE id=?',(key,))
                continue
            if old['id'] not in by_id:
                retained = dict(old,timeframes=[frame],scan_strategies=[STRATEGY],rsi_pb_followup_only=True)
                config['assets'].append(retained)
                by_id[old['id']] = retained
            elif by_id[old['id']].get('rsi_pb_followup_only') and frame not in by_id[old['id']]['timeframes']:
                by_id[old['id']]['timeframes'].append(frame)
    return config


def cancel_changed(db, old_assets, config):
    """Owner edits cancel affected waits and force a fresh baseline on re-enable."""
    from managed_assets import SCAN_STRATEGIES, for_timeframe
    initialize(db)
    new = {a['id']:a for a in config['assets']}
    for old in old_assets:
        current = new.get(old['id'])
        for frame in old.get('timeframes',config.get('timeframes',[])):
            changed = (not current or not current.get('enabled',True)
                       or frame not in current.get('timeframes',config.get('timeframes',[]))
                       or STRATEGY not in current.get('scan_strategies',SCAN_STRATEGIES)
                       or source_for(for_timeframe(old,frame)) != source_for(for_timeframe(current,frame)))
            if changed:
                prefix = old['id']+'@'
                for feed, in db.execute('SELECT feed FROM rsi_pb_state WHERE timeframe=?',(frame,)).fetchall():
                    if feed.startswith(prefix):
                        db.execute('DELETE FROM rsi_pb_pending WHERE feed=? AND timeframe=?',(feed,frame))
                        db.execute('DELETE FROM rsi_pb_state WHERE feed=? AND timeframe=?',(feed,frame))


def source_for(asset):
    return f"{asset['provider']}:{asset.get('exchange') or 'configured'}:{asset.get('symbol') or asset['id']}"


def view(store, page=0, page_size=50):
    from candle_strategy import view as shared_view
    import sys
    return shared_view(store,page,page_size,engine=sys.modules[__name__])


def message(signal):
    def candle_range(opened_at,closed_at):
        zone = ZoneInfo('Asia/Jerusalem')
        opened,closed = (datetime.fromtimestamp(t/1000,zone) for t in (opened_at,closed_at))
        return f"{opened:%d.%m.%y %H:%M}–{closed.strftime('%H:%M' if opened.date()==closed.date() else '%d.%m.%y %H:%M')}"
    refs = signal['divergences']
    lines = [f"{signal['direction'].upper()} RSI + PB",f"{signal['symbol']} · {signal['timeframe']}",
             f"Opposite pinbar: {signal['pinbar_direction'].upper()}"]
    for ref in refs[:3]:
        lines.append(f"RSI: {ref['rsi1']:.2f} → {ref['rsi2']:.2f} · Price: {ref['price1']:g} → {ref['price2']:g}")
        lines.append('Reference: '+candle_range(ref['pivot1'],ref['pivot1_closed_at']))
    if len(refs)>3:
        lines.append(f'{len(refs)} qualifying reference pairs; full evidence in Signals')
    lines += ['Divergence: '+candle_range(signal['pivot2'],signal['pivot_closed_at']),
              'Pinbar: '+candle_range(signal['candle_at'],signal['confirmed_at']),f"Source: {signal['source']}"]
    return '\n'.join(lines)
