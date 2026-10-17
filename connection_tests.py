"""Explicit local-dashboard connectivity tests; never enqueue trades or alerts."""
import hashlib
import json
import os
import time
import urllib.request
import uuid

from app_version import VERSION


class ConnectionTestError(RuntimeError):
    """An error safe to display without disclosing credentials or remote bodies."""


def run(action, rules):
    if action == 'telegram':
        token=os.environ.get('TELEGRAM_BOT_TOKEN','')
        chat=os.environ.get('TELEGRAM_CHAT_ID','')
        if not token or not chat:
            raise ConnectionTestError('Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env, then restart the app.')
        from platform_app import send_telegram
        from runtime_env import environment
        try:
            send_telegram(token,chat,f'Family Trading Bot {VERSION} — Telegram connection test ({environment()}). This is a test message, not a trading signal.')
        except Exception:
            raise ConnectionTestError('Telegram test failed. Check the bot token, chat ID, bot permissions and internet connection.') from None
        return dict(ok=True,message='Telegram test message sent to your configured chat.')
    if action != 'cloud-demo':
        raise ValueError('Unknown connection test')
    url=os.environ.get('CLOUD_SYNC_URL','')
    token=os.environ.get('CLOUD_SYNC_TOKEN','')
    if not url.startswith('https://') or not token:
        raise ConnectionTestError('Set a HTTPS CLOUD_SYNC_URL and CLOUD_SYNC_TOKEN in .env, then restart the app.')
    from finding_feedback import canonical
    now=int(time.time()*1000)
    key=hashlib.sha256(('cloud-demo:'+uuid.uuid4().hex).encode()).hexdigest()
    signal=dict(symbol='DEMO-CLOUD-SYNC',timeframe='1h',direction='bullish',
                pivot1=now-3*3600000,pivot2=now-2*3600000,confirmed_at=now,
                price1=100.0,price2=99.0,rsi1=15.0,rsi2=25.0,band1=101.0,band2=100.0,
                spacing=2,rules=rules,signal_status='confirmed',is_demo=True,
                note='DEMO: cloud connectivity test. Not a trading signal.')
    payload=dict(id=key,signal=signal,followups={},status='demo',attempts=0,error='',is_demo=True)
    event=hashlib.sha256(('live\0'+key+'\0'+canonical(payload)).encode()).hexdigest()
    body=dict(records=[dict(kind='live',key=key,event_id=event,payload=payload)],config_base=0,cursor=0,state_cursor=0)
    request=urllib.request.Request(url,data=canonical(body).encode(),
                                   headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},method='POST')
    try:
        with urllib.request.urlopen(request,timeout=10) as response:
            raw=response.read(2*1024*1024+1)
        if len(raw)>2*1024*1024:
            raise ValueError('Response too large')
        result=json.loads(raw)
        if not isinstance(result,dict) or event not in result.get('ack',[]):
            raise ValueError('Demo write was not acknowledged')
    except Exception:
        raise ConnectionTestError('Cloud demo sync could not be confirmed. Check the cloud connection and database schema; the demo may already have been saved.') from None
    return dict(ok=True,id=key,message='One demo signal synced to the cloud database. Look for DEMO-CLOUD-SYNC in Live signals.')
