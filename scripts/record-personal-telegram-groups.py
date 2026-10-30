"""Read and verify newly created personal groups; never sends messages."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from platform_app import load_env
from telegram_checklist import api


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--telegram-user-id',required=True,type=int)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    load_env()
    token=os.environ['TELEGRAM_BOT_TOKEN']
    original=json.loads(Path('telegram-group-map.json').read_text())
    bot=api(token,'getMe',{})
    if bot.get('username','').lower()!=original['bot_username'].lower():raise SystemExit('Bot mismatch')
    if api(token,'getWebhookInfo',{}).get('url'):raise SystemExit('Existing webhook; do not consume updates')
    updates=api(token,'getUpdates',dict(timeout=0,limit=100,allowed_updates=['message','callback_query','my_chat_member']))
    expected={'_'+g['name']:g['route'] for g in original['groups']}
    found={}
    for update in updates:
        for key in ('message','my_chat_member'):
            chat=update.get(key,{}).get('chat',{})
            title=chat.get('title')
            if title in expected:
                if title in found and found[title]!=str(chat['id']):raise SystemExit('Duplicate personal group name: '+title)
                found[title]=str(chat['id'])
    missing=set(expected)-set(found)
    if missing:raise SystemExit('Missing personal groups: '+', '.join(sorted(missing)))
    def verify(entry):
        name,chat=entry
        count=api(token,'getChatMemberCount',{'chat_id':chat})
        person=api(token,'getChatMember',{'chat_id':chat,'user_id':args.telegram_user_id})
        bot_member=api(token,'getChatMember',{'chat_id':chat,'user_id':bot['id']})
        if count!=2 or person.get('status') not in ('creator','administrator','member') or bot_member.get('status') not in ('administrator','member'):
            raise RuntimeError('Personal group membership mismatch: '+name)
        return dict(route=expected[name],name=name,chat_id=chat,members_verified=True)
    with ThreadPoolExecutor(max_workers=3) as pool:groups=list(pool.map(verify,found.items()))
    mapping=dict(bot_username=bot['username'],telegram_user_id=args.telegram_user_id,groups=groups,
                 routes={g['route']:g['chat_id'] for g in groups})
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(mapping,indent=2)+'\n');args.output.chmod(0o600)
    print('Verified and saved',len(groups),'personal groups to',args.output)

if __name__=='__main__':
    try:main()
    except RuntimeError as exc:raise SystemExit(str(exc)) from None
