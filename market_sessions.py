"""Evidence-backed request eligibility; never infer a closure from missing quotes.

All public timestamps are milliseconds UTC. Broker calendars govern requests only;
this module does not relax reader validation or invent historical candles.
"""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from market_calendar import (NY, forex_market_open, next_forex_open, is_forex,
                             stock_trading_day, stock_close)

VERSION = 'market-sessions-2026-11-14-v1'
REVIEWED = '2026-11-14'
COLMEX_NOTICE = 'https://www.colmex.co.il/wp-content/uploads/2026/11/Update_November_2026_Clients.pdf'
COLMEX_SPEC = 'https://www.colmexpro.com/wp-content/uploads/2023/12/Contract-Specifications-Colmex-Pro-Ltd-Dec-2023-CFDs.pdf'
# Minutes of local day, Monday=0; end 1440 is the following midnight.
# TGLColmex terminal specifications recorded 2026-11-06 take precedence over
# the older Colmex Pro (different entity) PDF. Native history corroborates these
# breaks and the Berlin/New York seasonal zones; see TELEGRAM_ALERT_LOGIC.md.
COLMEX = {
    'GER40#': ('Europe/Berlin', {d: ((130, 1320),) for d in range(5)}),
    'SPX500#': ('America/New_York', {
        **{d: ((0, 975), (990, 1020), (1085, 1440)) for d in range(4)},
        4: ((0, 975), (990, 1020)), 6: ((1140, 1440),)}),
    'USDINDEX#': ('America/New_York', {
        **{d: ((0, 1020), (1200, 1440)) for d in range(4)},
        4: ((0, 1020),), 6: ((1200, 1440),)}),
    'USOIL#': ('Europe/Berlin', {
        **{d: ((120, 1440),) for d in range(4)}, 4: ((120, 1320),)}),
}
# Exact UTC date replacements, converted from the notice's Israel-time table.
# SPX's extra Friday 01:00-02:00 Israel window starts Thursday 23:00 UTC.
# Future holidays require another reviewed notice, never a US-equity assumption.
# USOIL observations establish winter hours only. Reverify before Europe's
# next DST change instead of assuming CET in the old PDF means seasonal time.
USOIL_VALID_FROM = int(datetime(2026, 11, 6, tzinfo=timezone.utc).timestamp()*1000)
USOIL_VALID_UNTIL = int(datetime(2027, 3, 28, 1, tzinfo=timezone.utc).timestamp()*1000)

COLMEX_EXCEPTIONS = {
    ('SPX500#', '2026-11-26'): ((0, 1080), (1380, 1440)),
    ('SPX500#', '2026-11-27'): ((0, 1095),),
    ('USDINDEX#', '2026-11-26'): ((60, 1095),),
    ('USDINDEX#', '2026-11-27'): ((60, 1095),),
    ('USOIL#', '2026-11-26'): ((60, 1110),),
    ('USOIL#', '2026-11-27'): ((60, 1200),),
}


def _stamp(day, minute, zone):
    return int((datetime.combine(day, time(), zone) + timedelta(minutes=minute)).timestamp()*1000)


def _colmex_windows(symbol, now):
    zone_name, weekly = COLMEX[symbol]
    zone = ZoneInfo(zone_name)
    day = datetime.fromtimestamp(now/1000, zone).date()
    windows = []
    for offset in range(-1, 10):
        local_day = day + timedelta(days=offset)
        for start, end in weekly.get(local_day.weekday(), ()):
            a, b = _stamp(local_day, start, zone), _stamp(local_day, end, zone)
            # Remove overridden UTC dates, including windows crossing midnight.
            segments = [(a, b)]
            for (name, date_text), _ in COLMEX_EXCEPTIONS.items():
                if name != symbol:
                    continue
                midnight = datetime.fromisoformat(date_text).replace(tzinfo=timezone.utc)
                lo, hi = int(midnight.timestamp()*1000), int((midnight+timedelta(days=1)).timestamp()*1000)
                segments = [(x, y) for left, right in segments
                            for x, y in ((left, min(right, lo)), (max(left, hi), right)) if x < y]
            windows.extend(segments)
    for (name, date_text), sessions in COLMEX_EXCEPTIONS.items():
        if name == symbol:
            date = datetime.fromisoformat(date_text).date()
            windows.extend((_stamp(date, a, timezone.utc), _stamp(date, b, timezone.utc)) for a, b in sessions)
    return sorted(windows)


def _result(state, calendar, now, next_open=None, reason=''):
    return dict(session_state=state, session_calendar=calendar, session_version=VERSION,
                session_reviewed=REVIEWED, next_open_at=next_open,
                session_reason=reason)


def state(asset, now):
    """Unknown means keep validation/retries, never falsely label a feed closed."""
    provider = asset.get('provider')
    if provider == 'csv':
        return _result('not applicable', 'local-history', now)
    if provider == 'colmex':
        if asset.get('exchange') != 'Colmex-MT4':
            return _result('unknown', 'unmapped', now, reason='Exact broker exchange is not verified')
        symbol = asset.get('symbol')
        if symbol == 'USOIL#' and not USOIL_VALID_FROM <= now < USOIL_VALID_UNTIL:
            return _result('unknown', 'TGLColmex/USOIL#', now, reason='USOIL seasonal hours require verification')
        if symbol in COLMEX:
            windows = _colmex_windows(symbol, now)
            calendar = 'TGLColmex/' + symbol
            if any(a <= now < b for a, b in windows):
                return _result('open', calendar, now)
            opening = min(a for a, b in windows if a > now)
            if symbol == 'USOIL#' and opening >= USOIL_VALID_UNTIL:
                return _result('unknown', calendar, now, reason='USOIL next session crosses an unverified seasonal boundary')
            return _result('closed', calendar, now, opening, 'Scheduled broker session break or closure')
        # Requested pairs carry the broker catalogue's forex classification.
        if not is_forex(asset):
            return _result('unknown', 'unmapped', now, reason='No verified schedule for this exact Colmex symbol')
    if is_forex(asset):
        opened = forex_market_open(now)
        return _result('open' if opened else 'closed', 'forex-New_York', now,
                       None if opened else next_forex_open(now), 'Forex weekly session')
    if asset.get('market') == 'crypto' or provider == 'kraken':
        return _result('open', 'crypto-24x7', now)
    # Do not apply US equity hours to other exchanges or commodity/CFD feeds.
    if provider == 'alpaca' or asset.get('exchange') in ('NYSE', 'NASDAQ', 'AMEX', 'NYSE ARCA'):
        local = datetime.fromtimestamp(now/1000, NY)
        day = local.date()
        if stock_trading_day(day):
            opening, close = _stamp(day, 570, NY), int(stock_close(day).timestamp()*1000)
            if opening <= now < close:
                return _result('open', 'US-equities', now)
            # Existing bounded SIP final-bar allowance, never labelled market open.
            if close <= now < close + 20*60000:
                return _result('closing data', 'US-equities', now,
                               reason='Final completed equity bars; 20-minute delivery allowance')
        while not stock_trading_day(day) or _stamp(day, 570, NY) <= now:
            day += timedelta(days=1)
        return _result('closed', 'US-equities', now, _stamp(day, 570, NY), 'Scheduled equity session closure')
    return _result('unknown', 'unmapped', now, reason='No verified provider/instrument session mapping')


def next_request_time(asset, value):
    session = state(asset, value)
    return session['next_open_at'] if session['session_state'] == 'closed' else value


def closed_row(asset, now):
    session = state(asset, now)
    if session['session_state'] != 'closed':
        return None
    opening = session['next_open_at']
    label = datetime.fromtimestamp(opening/1000, timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    return session | dict(status='market closed', check_state='waiting for market open',
                          reason=f"{session['session_reason']}; next open {label}",
                          checked_at=now, next_close_at=opening, new_signals=0)


def classify_unavailable(asset, now, reason=""):
    """A calendar match is evidence, not proof that the terminal was healthy."""
    session = state(asset, now)
    classification = {'closed': 'expected closure', 'unknown': 'schedule unverified',
                      'open': 'open-session data failure', 'closing data': 'closing-data failure'}
    label = classification.get(session['session_state'], 'data failure')
    if session['session_state'] == 'closed' and reason:
        label = ('closure-compatible stale quote' if 'quote is stale' in reason
                 else 'data failure during closure')
    return session | dict(availability_classification=label)


class MarketClosed(ValueError):
    def __init__(self, row):
        self.row = row
        super().__init__(row['reason'])


def require_open(asset, now):
    row = closed_row(asset, now)
    if row:
        raise MarketClosed(row)
