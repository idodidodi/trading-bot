import json
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import psycopg
from test_postgres_support import PostgresTestCase, TestStore, test_dsn
from telegram_personal import deliver_personal, process
from telegram_dismissal import DismissibleMessage
from telegram_checklist import bot_key

INST='00000000-0000-0000-0000-000000000002'
OWNER='00000000-0000-0000-0000-000000000001'
OTHER='00000000-0000-0000-0000-000000000003'
STRANGER='00000000-0000-0000-0000-000000000004'


class PersonalTelegramTests(PostgresTestCase):
    def setUp(self):
        self.store=TestStore()
        with self.store.connect() as db:
            db.execute('DELETE FROM public.telegram_recipients WHERE installation=%s',(INST,))
            db.execute('DELETE FROM public.telegram_alert_preferences WHERE installation=%s',(INST,))
            db.execute('INSERT INTO auth.users VALUES(%s),(%s) ON CONFLICT DO NOTHING',(OTHER,STRANGER))
            db.execute("INSERT INTO public.dashboard_members VALUES(%s,%s,'viewer',now()) ON CONFLICT DO NOTHING",(INST,OTHER))
            db.execute("INSERT INTO telegram_event_routes VALUES('event','forex','rsi','-1','-1')")

    def api(self,user,body=None):
        with psycopg.connect(test_dsn()) as db:
            db.execute('SELECT set_config(\'test.auth_uid\',%s,true)',(user,))
            db.execute('SET LOCAL ROLE authenticated')
            return db.execute('SELECT public.dashboard_api(%s,%s,%s::jsonb)',(INST,'/api/telegram-alerts',json.dumps(body or {}))).fetchone()[0]

    def register(self,user,chat,telegram_id):
        with self.store.connect() as db:
            db.execute('INSERT INTO public.telegram_recipients(installation,user_id,telegram_user_id,routes) VALUES(%s,%s,%s,%s::jsonb)',
                       (INST,user,telegram_id,json.dumps({'forex.rsi':chat})))

    def test_defaults_and_viewer_can_change_only_own_preferences(self):
        original=self.api(OTHER)
        self.assertEqual(len(original['enabled']),13)
        self.assertTrue(all(original['enabled'].values()))
        changed=self.api(OTHER,dict(action='save',revision=0,enabled={'forex.rsi':False}))
        self.assertFalse(changed['enabled']['forex.rsi'])
        self.assertTrue(self.api(OWNER)['enabled']['forex.rsi'])
        with self.assertRaises(psycopg.Error): self.api(STRANGER)
        with self.assertRaises(psycopg.Error): self.api(OTHER,dict(action='save',revision=1,enabled={},user_id=OWNER))
        with self.assertRaises(psycopg.Error): self.api(OTHER,dict(action='save',revision=0,enabled={}))

    def test_invalid_settings_and_base_tables_are_rejected(self):
        for enabled in ({'bad':True},{'general':'false'}):
            with self.assertRaises(psycopg.Error): self.api(OTHER,dict(action='save',revision=0,enabled=enabled))
        with psycopg.connect(test_dsn()) as db:
            db.execute('SET LOCAL ROLE authenticated')
            with self.assertRaises(psycopg.Error): db.execute('SELECT * FROM public.telegram_recipients')

    @patch('telegram_delivery.deliver')
    def test_disabled_event_stays_skipped_after_resume_others_are_independent(self,deliver):
        self.register(OWNER,'-2',123)
        self.register(OTHER,'-3',456)
        self.api(OWNER,dict(action='save',revision=0,enabled={'forex.rsi':False}))
        deliver_personal(self.store,'token','text','event',Mock())
        self.assertEqual(deliver.call_count,1)
        self.assertEqual(deliver.call_args.args[2],'-3')
        self.api(OWNER,dict(action='save',revision=1,enabled={'forex.rsi':True}))
        deliver.reset_mock()
        deliver_personal(self.store,'token','text','event',Mock())
        self.assertEqual([call.args[2] for call in deliver.call_args_list],['-3'])
        with self.store.connect() as db:db.execute("INSERT INTO telegram_event_routes VALUES('new','forex','rsi','-1','-1')")
        deliver.reset_mock()
        deliver_personal(self.store,'token','text','new',Mock())
        self.assertEqual({call.args[2] for call in deliver.call_args_list},{'-2','-3'})

    @patch('telegram_delivery.deliver')
    def test_missing_personal_category_never_falls_back_to_shared_group(self,deliver):
        self.register(OWNER,'-2',123)
        with self.store.connect() as db:db.execute("UPDATE telegram_event_routes SET strategy='pin-bars'")
        deliver_personal(self.store,'token','text','event',Mock())
        deliver.assert_not_called()

    @patch('telegram_checklist.api')
    @patch('postgres_store.assert_worker')
    def test_done_and_undo_persist_and_only_personal_owner_can_act(self,guard,api):
        item=DismissibleMessage(-2,42,{'text':'original','entities':[]},user_id=123)
        with self.store.connect() as db:db.execute('INSERT INTO telegram_personal_messages VALUES(%s,%s,%s)',(item.key,bot_key('token'),json.dumps(item.snapshot())))
        query=dict(id='q',data='dismiss:done:'+item.key,message={'chat':{'id':-2},'message_id':42},**{'from':{'id':456}})
        process(self.store,'token',query)
        api.assert_not_called()
        query['from']['id']=123
        process(self.store,'token',query)
        with self.store.connect() as db:state=json.loads(db.execute('SELECT state FROM telegram_personal_messages').fetchone()[0])
        self.assertEqual(state['phase'],'pending')
        query['data']='dismiss:undo:'+item.key
        process(self.store,'token',query)
        with self.store.connect() as db:state=json.loads(db.execute('SELECT state FROM telegram_personal_messages').fetchone()[0])
        self.assertEqual(state['phase'],'active')

class OriginalGroupTests(unittest.TestCase):
    @patch('telegram_personal.prepare_message')
    @patch('telegram_checklist.enabled',return_value=False)
    @patch('platform_app.Store')
    def test_shared_group_message_has_done_without_an_owner_restriction(self,store,enabled,prepare):
        import io
        from platform_app import send_telegram
        from telegram_personal import prepare_message
        item=DismissibleMessage(-12,0,{'text':'sample','entities':[]},user_id=None)
        prepare.return_value=item
        with patch('platform_app.urllib.request.urlopen',return_value=io.StringIO(json.dumps({'ok':True,'result':{'message_id':42,'chat':{'id':-12}}}))), patch('telegram_personal.bind_message'):
            send_telegram('token','-12','sample',dismissal_user_id=0)
        self.assertEqual(prepare.call_args.args[-1],0)

    @patch('telegram_delivery.deliver',return_value='sent')
    @patch('telegram_routing.select',return_value=('-12','-12'))
    @patch('telegram_personal.deliver_personal')
    def test_shared_button_flag_wraps_only_new_routed_sends(self,personal,select,deliver):
        from telegram_routing import deliver_routed
        sender=Mock();store=Mock()
        with patch.dict(os.environ,TELEGRAM_GROUP_DISMISSAL_ENABLED='true'):
            deliver_routed(store,'token','-12','new alert','new-event',sender)
        wrapped=deliver.call_args.args[5]
        wrapped('token','-12','new alert')
        sender.assert_called_once_with('token','-12','new alert',dismissal_user_id=0)
        self.assertIs(personal.call_args.args[4],sender)

class DemoDestinationTests(unittest.TestCase):
    @patch('telegram_personal.deliver_personal')
    @patch('telegram_routing.select',return_value=('123','123'))
    @patch('telegram_delivery.deliver',return_value='sent')
    def test_explicit_demo_does_not_fan_out_to_personal_groups(self,deliver,select,personal):
        from telegram_routing import deliver_routed
        store=Mock();sender=Mock()
        result=deliver_routed(store,'token','shared','Test','notification:telegram-demo:unique',sender,
                              notification_key='telegram-demo:unique')
        self.assertEqual(result,'sent')
        self.assertEqual(deliver.call_args.args[2],'123')
        personal.assert_not_called()


if __name__ == '__main__':
    unittest.main()
