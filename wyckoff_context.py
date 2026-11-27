"""Causal, conservative Wyckoff cycle estimate, not a schematic event detector."""
from statistics import mean
VERSION='wyckoff-estimate-v1'


def analyze(candles,volumes,at):
    bars=[c for c in candles if c.end<=at][-60:]
    result=dict(version=VERSION,stage='Unclear',estimated=True,as_of=at,volume_available=False)
    if len(bars)<40:return result|{'reason':'Need 40 closed candles'}
    recent=bars[-20:];prior=bars[-40:-20]
    atr=mean(c.high-c.low for c in recent)
    if atr<=0:return result|{'reason':'Flat price range'}
    move=recent[-1].close-recent[0].close
    distance=sum(abs(b.close-a.close) for a,b in zip(recent,recent[1:]))
    efficiency=abs(move)/distance if distance else 0
    available=all(c.start in volumes for c in bars[-40:]) and sum(volumes[c.start] for c in bars[-40:])>0
    result.update(volume_available=available,efficiency=round(efficiency,4),move_atr=round(move/atr,4))
    if abs(move)>=3*atr and efficiency>=.35:
        result['stage']='Markup' if move>0 else 'Markdown'
    elif abs(move)<=2*atr and max(c.high for c in recent)-min(c.low for c in recent)<=8*atr and available:
        # A range after a directional move needs volume/close-location evidence.
        signed=sum(volumes[c.start]*((2*c.close-c.high-c.low)/(c.high-c.low) if c.high>c.low else 0) for c in recent)
        total=sum(volumes[c.start] for c in recent)
        pressure=signed/total if total else 0
        prior_move=prior[-1].close-prior[0].close
        result['volume_pressure']=round(pressure,4)
        if prior_move<=-3*atr and pressure>=.1:result['stage']='Accumulation'
        elif prior_move>=3*atr and pressure<=-.1:result['stage']='Distribution'
    result['reason']='Price and volume heuristic' if available else 'Price-only; volume unavailable'
    return result


def line(signal):
    value=signal.get('wyckoff')
    if not value:return ''
    suffix='' if value['volume_available'] else '; no volume'
    return f"\nWyckoff stage (estimated{suffix}): {value['stage']}"
