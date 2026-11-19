"""Deterministic OHLC diagrams from the closed candles saved with a pin bar."""
import io
import json
import math
from functools import partial


def snapshot(candles, candle_at):
    return [dict(start=b.start, open=b.open, high=b.high, low=b.low, close=b.close)
            for b in candles if b.start <= candle_at][-24:]


def render(signal):
    from PIL import Image, ImageDraw, ImageFont
    bars = signal.get('chart_candles') or [dict(start=signal['candle_at'], **{
        k: signal[k] for k in ('open', 'high', 'low', 'close')})]
    if not bars or bars[-1]['start'] != signal['candle_at']:
        raise ValueError('Chart must end at the signal candle')
    for b in bars:
        if (not all(math.isfinite(b[k]) for k in ('open', 'high', 'low', 'close'))
                or not b['low'] <= min(b['open'], b['close']) <= max(b['open'], b['close']) <= b['high']):
            raise ValueError('Invalid chart OHLC')
    image = Image.new('RGB', (1200, 720), '#101b2b')
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=23)
    small = ImageFont.load_default(size=18)
    draw.text((45, 28), f"{signal['symbol']}  |  {signal['timeframe']}  |  {signal['direction'].upper()} PIN BAR",
              fill='#edf4ff', font=font)
    draw.text((45, 66), 'Closed candles · outlined candle = signal', fill='#a8bad1', font=small)
    lo, hi = min(b['low'] for b in bars), max(b['high'] for b in bars)
    pad = max((hi-lo)*.12, abs(hi)*.001, 1e-10)
    lo, hi = lo-pad, hi+pad
    def y(value): return round(580-(value-lo)/(hi-lo)*450)
    for i in range(6):
        value = lo+(hi-lo)*i/5
        yy = y(value)
        draw.line((45, yy, 1040, yy), fill='#29394e')
        draw.text((1050, yy-10), f'{value:.7g}', fill='#a8bad1', font=small)
    step = 970/max(len(bars), 8)
    for i, b in enumerate(bars):
        x = round(65 + i*step + step/2)
        color = '#41d6a4' if b['close'] >= b['open'] else '#ff7185'
        draw.line((x, y(b['high']), x, y(b['low'])), fill=color, width=3)
        top, bottom = sorted((y(b['open']), y(b['close'])))
        width = min(24, int(step*.3))
        draw.rectangle((x-width, top, x+width, max(top+2, bottom)), fill=color)
        if i == len(bars)-1:
            draw.rounded_rectangle((x-width-7, y(b['high'])-9, x+width+7, y(b['low'])+9),
                                   radius=6, outline='#ffd46b', width=3)
    from datetime import datetime
    from zoneinfo import ZoneInfo
    stamp = datetime.fromtimestamp(signal['confirmed_at']/1000, ZoneInfo('Asia/Jerusalem')).strftime('%d %b %Y %H:%M')
    draw.text((45, 615), f"O {signal['open']:.7g}    H {signal['high']:.7g}    L {signal['low']:.7g}    C {signal['close']:.7g}", fill='#edf4ff', font=font)
    draw.text((45, 660), f'Confirmed {stamp} Israel time · {len(bars)} closed candles', fill='#a8bad1', font=small)
    output = io.BytesIO()
    image.save(output, format='PNG')
    return output.getvalue()


def notification_sender(store, key, send):
    strategy, _, identity = key.partition(':')
    table = {'pin-bars': 'pin_bar_signals', 'bb-pin-bars': 'bb_pin_bar_signals'}.get(strategy)
    if not table:
        return send
    with store.connect() as db:
        row = db.execute(f'SELECT payload FROM {table} WHERE id=%s', (identity,)).fetchone()
    if not row:
        raise RuntimeError('Pin-bar chart signal missing')
    return partial(send, photo=render(json.loads(row[0])))


def photo_request(token, payload, photo):
    import uuid
    import urllib.request
    boundary = 'pinbar-' + uuid.uuid4().hex
    parts = []
    payload = dict(payload)
    payload['caption'] = payload.pop('text')
    payload['caption_entities'] = payload.pop('entities')
    if len(payload['caption'].encode('utf-16-le')) // 2 > 1024:
        raise ValueError('Pin-bar caption exceeds Telegram limit')
    for key, value in payload.items():
        value = value if isinstance(value, str) else json.dumps(value)
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="pinbar.png"\r\nContent-Type: image/png\r\n\r\n'.encode() + photo + b'\r\n')
    parts.append(f'--{boundary}--\r\n'.encode())
    return urllib.request.Request(f'https://api.telegram.org/bot{token}/sendPhoto', data=b''.join(parts),
        headers={'Content-Type': 'multipart/form-data; boundary=' + boundary})
