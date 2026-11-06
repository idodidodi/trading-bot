"""One in-flight request/result per exact Kraken candle snapshot, across workers."""
from concurrent.futures import Future
from threading import Lock
import time


class SnapshotCache:
    def __init__(self):
        self.lock = Lock()
        self.entries = {}

    def clear(self):
        with self.lock:
            self.entries.clear()

    def get(self, key, now, build, lifetime):
        with self.lock:
            entry = self.entries.get(key)
            if entry and (not entry[1].done() or now < entry[0]):
                future = entry[1]
                owner = False
            else:
                future = Future()
                self.entries[key] = (float('inf'), future)
                owner = True
        if not owner:
            return future.result()
        try:
            value = build()
            until = lifetime(value)
        except Exception as exc:
            with self.lock:
                self.entries[key] = (now + 15000, future)
            future.set_exception(exc)
            raise
        with self.lock:
            self.entries[key] = (until, future)
            if len(self.entries) > 512:
                for old_key, (_, old) in list(self.entries.items()):
                    if old_key != key and old.done():
                        del self.entries[old_key]
                        if len(self.entries) <= 384:
                            break
        future.set_result(value)
        return value


candles = SnapshotCache()
divergences = SnapshotCache()


def fetch(asset, timeframe, now=None):
    from kraken_feed import fetch_kraken, INTERVALS, validate_feed
    validate_feed(asset, timeframe)
    now = int(time.time() * 1000) if now is None else now
    duration = INTERVALS[timeframe] * 60000
    boundary = now // duration * duration
    key = (asset['symbol'], asset['exchange'], timeframe)
    def lifetime(rows):
        # An empty/delayed response is reusable briefly, never for a whole bar.
        return boundary + duration if rows and rows[-1].end >= boundary else now + 15000
    return candles.get(key, now, lambda: fetch_kraken(asset, timeframe), lifetime)


def detect(rows, rules, symbol, timeframe, now):
    from scanner import detect as engine
    import json
    evidence = tuple((c.start,c.end,c.open,c.high,c.low,c.close) for c in rows)
    key = (symbol,timeframe,json.dumps(rules,sort_keys=True),hash(evidence))
    return divergences.get(key,now,lambda:engine(rows,rules,symbol,timeframe,provisional=True),lambda _:now+60000)
