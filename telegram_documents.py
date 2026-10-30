"""Routed PDF attachment delivery using existing durable Telegram reservations."""
import json
import os
import urllib.error
import urllib.request
import uuid


def send_document(token, chat, caption, filename, content, silent=False, source_identity=None, dismissal_user_id=None):
    # Routed personal/group deliveries supply this context. Their text-only
    # dismissal editor cannot edit PDF messages; keep attachments intact.
    from platform_app import TelegramDestinationError
    from telegram_delivery import footer
    if not token:
        raise RuntimeError('Configure TELEGRAM_BOT_TOKEN')
    if source_identity:
        caption=footer(caption,source_identity)
    boundary='report-'+uuid.uuid4().hex
    parts=[]
    for name,value in {'chat_id':chat,'caption':caption,'disable_notification':str(silent).lower()}.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    # Filenames are generated internally, never interpolated from provider data.
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{filename}"\r\nContent-Type: application/pdf\r\n\r\n'.encode()+content+b'\r\n')
    parts.append(f'--{boundary}--\r\n'.encode())
    request=urllib.request.Request(f'https://api.telegram.org/bot{token}/sendDocument',data=b''.join(parts),
                                   headers={'Content-Type':'multipart/form-data; boundary='+boundary})
    try:
        with urllib.request.urlopen(request,timeout=60) as response:
            result=json.load(response)
        message_id=result.get('result',{}).get('message_id')
        if not result.get('ok') or not message_id:
            raise RuntimeError('Telegram rejected document')
        return message_id
    except urllib.error.HTTPError as exc:
        if exc.code in (400,403):
            try:
                reason=json.load(exc).get('description','').lower()
            except Exception:
                reason=''
            if any(term in reason for term in ('chat not found','bot was kicked','bot was blocked','not enough rights','bot is not a member','group chat was deleted')):
                raise TelegramDestinationError('Telegram destination unavailable') from None
        raise RuntimeError(f'Telegram HTTP {exc.code}') from None
    except (OSError,ValueError):
        raise RuntimeError('Telegram document request failed') from None


def deliver_document(store, event, filename, strategy, caption, content):
    from telegram_routing import remember, deliver_routed
    with store.connect() as db:
        remember(db,event,'stocks' if strategy else None,strategy)
    def send(token,chat,text,**kwargs):
        return send_document(token,chat,text,filename,content,**kwargs)
    return deliver_routed(store,os.environ.get('TELEGRAM_BOT_TOKEN',''),os.environ.get('TELEGRAM_CHAT_ID',''),
                          caption,event,send)
