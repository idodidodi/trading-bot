"""Closed-candle trend-pullback and reversal rejection pin bars."""
def load_rules():
    return dict(version='trend-pin-bars-v3',trend_candles=5,min_trend_steps=3,reversal_candles=10,
                max_pullback_candles=12,max_retracement=2/3,ratio=2.,max_short_wick_ratio=.66)


def detect(candles,rules,source,symbol,timeframe):
    signals=[]
    n=rules['trend_candles']
    for i in range(n+1,len(candles)):
        b=candles[i];body=abs(b.close-b.open)
        lower=min(b.open,b.close)-b.low;upper=b.high-max(b.open,b.close)
        direction=('bullish' if lower>rules['ratio']*body and lower>upper and upper<=lower*rules['max_short_wick_ratio']
                   else 'bearish' if upper>rules['ratio']*body and upper>lower and lower<=upper*rules['max_short_wick_ratio'] else None)
        if not direction:continue
        d=1 if direction=='bullish' else -1
        for length in range(1,rules['max_pullback_candles']+1):
            start=i-length-n
            if start<0:continue
            trend=candles[start:start+n];pullback=candles[start+n:i]
            move=d*(trend[-1].close-trend[0].close)
            ranges=sum(c.high-c.low for c in trend)/n
            if move<=ranges or sum(d*(c.close-a.close)>0 for a,c in zip(trend,trend[1:]))<rules['min_trend_steps']:continue
            previous=trend[-1].close
            # Anchor the impulse at its strongest close, then allow a choppy pullback.
            if any(d*(c.close-previous)>0 for c in trend):continue
            if any(d*(c.close-previous)>0 for c in pullback):continue
            if d*(pullback[-1].close-previous)>=0:continue
            # Include the pin's extreme: the pullback must remain small and intact.
            extreme=min(c.low for c in pullback+[b]) if d==1 else max(c.high for c in pullback+[b])
            retracement=d*(previous-extreme)/move
            if not 0<retracement<=rules['max_retracement']:continue
            if d*(b.close-previous)>0:continue  # Already beyond the prior trend extreme.
            signals.append(dict(strategy='trend-pin-bars',setup='trend_pullback',version=rules['version'],source=source,symbol=symbol,
                timeframe=timeframe,direction=direction,candle_at=b.start,confirmed_at=b.end,
                open=b.open,high=b.high,low=b.low,close=b.close,body=body,lower_wick=lower,upper_wick=upper,
                trend_start=trend[0].start,trend_end=trend[-1].start,pullback_start=pullback[0].start,
                pullback_candles=length,retracement=retracement,rules=dict(rules)))
            break
        if signals and signals[-1]['candle_at']==b.start:continue
        # A reversal rejects a new extreme after a meaningful opposite move.
        # The preceding candles and the pin itself are the only evidence used.
        lookback=rules['reversal_candles']
        if i<lookback:continue
        prior=candles[i-lookback:i]
        mean_range=sum(c.high-c.low for c in prior)/lookback
        prior_move=prior[-1].close-prior[0].close
        if -d*prior_move<=mean_range:continue
        if d==1 and b.low>min(c.low for c in prior):continue
        if d==-1 and b.high<max(c.high for c in prior):continue
        signals.append(dict(strategy='trend-pin-bars',setup='reversal',version=rules['version'],source=source,symbol=symbol,
            timeframe=timeframe,direction=direction,candle_at=b.start,confirmed_at=b.end,
            open=b.open,high=b.high,low=b.low,close=b.close,body=body,lower_wick=lower,upper_wick=upper,
            context_start=prior[0].start,context_end=prior[-1].start,prior_move=prior_move,rules=dict(rules)))
    return signals

STRATEGY='trend-pin-bars'
TABLE_PREFIX='trend_pin_bar'
COVERAGE_KEY='trend_pin_bars'


def minimum_history(rules):return rules['trend_candles']+2


def scan(store,asset,timeframe,candles,rules,now,selection_start=None,catchup=False):
    import sys
    from candle_strategy import scan as scan_strategy
    return scan_strategy(store,asset,timeframe,candles,rules,now,selection_start,catchup,engine=sys.modules[__name__])


def message(signal):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from wyckoff_context import line
    when=datetime.fromtimestamp(signal['confirmed_at']/1000,ZoneInfo('Asia/Jerusalem')).strftime('%d %b %Y %H:%M')
    setup="reversal" if signal.get("setup")=="reversal" else "trend pullback"
    return (f"{signal['direction'].upper()} {setup} pin bar\nNEAR/USD · {signal['timeframe']} · {signal['close']:g}\n"
            f"Closed: {when} Israel"+line(signal))
