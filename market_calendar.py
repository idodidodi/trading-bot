"""US regular sessions and the New York forex week, including DST.

NYSE scheduled holidays/early closes; unscheduled exchange closures require an
updated calendar. Futures require their own verified product session calendar.
"""
import calendar
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo('America/New_York')


def nth_weekday(year, month, weekday, n):
    first = date(year, month, 1)
    return first + timedelta(days=(weekday-first.weekday()) % 7 + (n-1)*7)


def easter(year):
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b+8)//25
    g = (b-f+1)//3
    h = (19*a+b-d-g+15) % 30
    i, k = c//4, c % 4
    l = (32+2*e+2*i-h-k) % 7
    m = (a+11*h+22*l)//451
    v = h+l-7*m+114
    return date(year, v//31, v % 31+1)


def observed(day):
    return day + timedelta(days=-1 if day.weekday()==5 else 1 if day.weekday()==6 else 0)


def stock_trading_day(day):
    y = day.year
    memorial = date(y, 5, 31)
    memorial -= timedelta(days=memorial.weekday())
    # NYSE does not observe a Saturday New Year's Day on the prior Friday.
    new_year = date(y, 1, 1)
    if new_year.weekday()==6:
        new_year += timedelta(days=1)
    holidays = {new_year, nth_weekday(y,1,0,3), nth_weekday(y,2,0,3),
                easter(y)-timedelta(days=2), memorial, observed(date(y,7,4)),
                nth_weekday(y,9,0,1), nth_weekday(y,11,3,4), observed(date(y,12,25))}
    if y >= 2022:
        holidays.add(observed(date(y,6,19)))
    # National day of mourning, declared by NYSE for President Carter.
    holidays.add(date(2025,1,9))
    return day.weekday()<5 and day not in holidays


def stock_close(day):
    thanksgiving = nth_weekday(day.year,11,3,4)
    early = day==thanksgiving+timedelta(days=1) or (day.month==12 and day.day==24)
    if day.month==7 and day.day==3:
        early = True
    return datetime.combine(day,time(13 if early else 16),NY)


def previous_session(day):
    while not stock_trading_day(day):
        day -= timedelta(days=1)
    return day


def stock_candle_end(start, timeframe):
    local=start.astimezone(NY)
    day=local.date()
    if timeframe=='monthly':
        day=date(day.year,day.month,calendar.monthrange(day.year,day.month)[1])
    elif timeframe=='weekly':
        day += timedelta(days=4-day.weekday())
    if timeframe in ('daily','weekly','monthly'):
        return stock_close(previous_session(day))
    end=local+timedelta(hours=1 if timeframe=='1h' else 4)
    return min(end,stock_close(day)) if time(9,30)<=local.time()<time(16) else end


def is_stock(asset):
    return asset.get('market') in ('stock','stocks','equity','equities','etf') or asset.get('exchange') in ('NASDAQ','NYSE','AMEX','NYSE ARCA') or asset.get('provider')=='alpaca'


def next_market_time(asset, value):
    """Move an intraday poll out of closed sessions; allow delayed closing data."""
    local=datetime.fromtimestamp(value/1000,NY)
    if is_stock(asset):
        # SIP closing bars can arrive sixteen minutes after the bell.
        if stock_trading_day(local.date()) and time(9,30)<=local.time()<= (stock_close(local.date())+timedelta(minutes=20)).time():
            return value
        day=local.date()
        if local.time()>=time(9,30):
            day+=timedelta(days=1)
        while not stock_trading_day(day):
            day+=timedelta(days=1)
        return int(datetime.combine(day,time(9,30),NY).timestamp()*1000)
    if asset.get('market')=='forex':
        if local.weekday()==5 or local.weekday()==4 and local.time()>=time(17,20) or local.weekday()==6 and local.time()<time(17):
            day=local.date()+timedelta(days=(6-local.weekday()) % 7)
            return int(datetime.combine(day,time(17),NY).timestamp()*1000)
    return value
