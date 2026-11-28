"""Regular-session US equity intraday candles, independent of stock RSI watches."""
from datetime import datetime, time, timedelta
from market_calendar import NY, stock_close, stock_trading_day

VERSION = 'rth-open-v1'
STEP = 30 * 60_000
FRAMES = {'1h': 60 * 60_000, '4h': 240 * 60_000}


def session(day):
    return (int(datetime.combine(day, time(9, 30), NY).timestamp()*1000),
            int(stock_close(day).timestamp()*1000))


def aggregate(bars, timeframe, cutoff, *, strict=True):
    """Aggregate complete half-hours only; never relabel a native hourly OHLC.

    Missing interior half-hours fail closed rather than bridge missing history.
    An incomplete oldest bucket can result from a requested/page boundary and is
    discarded. The still-forming latest bucket is never returned.
    """
    from scanner import Candle, ProviderCandles, check_candles
    period = FRAMES[timeframe]
    groups = {}
    sessions = set()
    for bar in check_candles(sorted(bars, key=lambda b: b.start), cutoff):
        day = datetime.fromtimestamp(bar.start/1000, NY).date()
        if not stock_trading_day(day):
            continue
        opening, closing = session(day)
        if not opening <= bar.start < closing:
            continue
        if (bar.start-opening) % STEP or bar.end-bar.start != STEP or bar.end > closing:
            raise ValueError('Invalid RTH half-hour source boundary')
        start = opening + (bar.start-opening)//period*period
        groups.setdefault((start, min(start+period, closing)), []).append(bar)
        sessions.add((opening, closing))
    result = []
    incomplete = 0
    for index, ((start, end), members) in enumerate(sorted(groups.items())):
        if end > cutoff:
            continue
        if [b.start for b in members] != list(range(start, end, STEP)):
            if strict and index != 0:
                raise ValueError('Incomplete RTH candle history; missing half-hour bars')
            incomplete += 1
            continue
        if strict and result and result[-1].end < start:
            previous_day = datetime.fromtimestamp(result[-1].start/1000, NY).date()
            if previous_day == datetime.fromtimestamp(start/1000, NY).date():
                raise ValueError('Incomplete RTH candle history; missing intraday bucket')
        result.append(Candle(start, end, members[0].open, max(b.high for b in members),
                             min(b.low for b in members), members[-1].close))
    output = ProviderCandles(result, [
        'RTH only; 09:30 New York alignment; half-hour OHLC aggregation; early closes and DST',
    ] + ([f'{incomplete} incomplete boundary bucket(s) excluded'] if incomplete else []))
    output.sessions = sorted(sessions)
    output.next_close_at = next_close(timeframe, cutoff)
    return output


def next_close(timeframe, cutoff):
    day = datetime.fromtimestamp(cutoff/1000, NY).date()
    for _ in range(370):
        if stock_trading_day(day):
            opening, closing = session(day)
            for start in range(opening, closing, FRAMES[timeframe]):
                end = min(start+FRAMES[timeframe], closing)
                if end > cutoff:
                    return end
        day += timedelta(days=1)
    raise ValueError('No future stock session found')
