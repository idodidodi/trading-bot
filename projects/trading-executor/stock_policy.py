"""Shared live stock restrictions; the scanner's schedule uses Israel time."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

MIN_PRICE = 5.0
ZONE = ZoneInfo('Asia/Jerusalem')


def scan_day(now):
    return datetime.fromtimestamp(now/1000,ZONE).weekday()<5


def next_scan(now):
    opening=datetime.fromtimestamp(now/1000,ZONE).replace(hour=8,minute=0,second=0,microsecond=0)
    if opening.timestamp()*1000<=now:opening+=timedelta(days=1)
    while opening.weekday()>=5:opening+=timedelta(days=1)
    return int(opening.timestamp()*1000)
