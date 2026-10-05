"""Local chat-ID lookup or explicit test message; never prints the bot token."""
import argparse
import json
import os
import urllib.error
import urllib.request
from platform_app import load_env, send_telegram, telegram_connection_error


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['chats', 'test'])
    args = parser.parse_args()
    load_env()
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    if not token:
        raise SystemExit('Add TELEGRAM_BOT_TOKEN to .env first.')
    if args.command == 'test':
        chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
        if not chat_id:
            raise SystemExit('Add TELEGRAM_CHAT_ID to .env first.')
        try:
            send_telegram(token, chat_id, 'Trading signal platform: Telegram connection test.')
        except RuntimeError as exc:
            raise SystemExit(str(exc)) from None
        print('Telegram test message sent.')
        return
    req = urllib.request.Request(f'https://api.telegram.org/bot{token}/getUpdates', data=b'{}', headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f'Telegram HTTP {exc.code}; check token/access. Token omitted.') from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise SystemExit(telegram_connection_error(exc)) from None
    if not result.get('ok'):
        raise SystemExit('Telegram rejected getUpdates.')
    chats = {}
    for update in result.get('result', []):
        event = update.get('message') or update.get('channel_post') or update.get('my_chat_member') or {}
        chat = event.get('chat', {})
        if 'id' in chat:
            chats[chat['id']] = chat.get('title') or chat.get('first_name') or chat.get('type')
    for chat_id, name in chats.items():
        print(f'{chat_id}: {name}')
    if not chats:
        print('No recent chats. Open the bot and press Start, or send /start@YourBot in your group, then retry. A bot already using Telegram webhooks cannot use getUpdates.')


if __name__ == '__main__':
    main()
