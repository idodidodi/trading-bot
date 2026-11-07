"""Notification preferences: disposable DB and mocked delivery only."""
from datetime import datetime
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import psycopg
from telegram_personal import personal_silent, deliver_personal
import test_telegram_personal as fixtures
from test_telegram_personal import INST, OWNER, OTHER, STRANGER
from test_postgres_support import test_dsn, PostgresTestCase


class QuietHoursTests(unittest.TestCase):
    def person(self, start='22:00', end='07:00', zone='Asia/Jerusalem'):
        return {'quiet_hours':dict(enabled=True,start=start,end=end,timezone=zone)}

    def test_overnight_boundaries_and_timezone(self):
        p=self.person()
        for instant, expected in [('2026-11-07T19:59:59+00:00',False),('2026-11-07T20:00:00+00:00',True),('2026-11-08T04:59:59+00:00',True),('2026-11-08T05:00:00+00:00',False)]:
            self.assertEqual(personal_silent(p,'general',datetime.fromisoformat(instant)),expected)

    def test_daytime_dst_and_or(self):
        p=self.person('09:00','17:00')
        self.assertTrue(personal_silent(p,'general',datetime.fromisoformat('2026-07-07T06:00:00+00:00')))
        self.assertFalse(personal_silent(p,'general',datetime.fromisoformat('2026-11-07T06:00:00+00:00')))
        p['quiet_hours']['enabled']=False
        p['silent']={'general':True}
        self.assertTrue(personal_silent(p,'general'))
        self.assertFalse(personal_silent(p,'forex.rsi'))
        self.assertFalse(personal_silent({},'general'))

    def test_dst_repeated_hour_and_invalid_legacy_zone(self):
        p=self.person('01:00','02:00','America/New_York')
        for hour in (5,6):
            self.assertTrue(personal_silent(p,'general',datetime.fromisoformat(f'2026-11-01T0{hour}:30:00+00:00')))
        p['quiet_hours']['timezone']='invalid/zone'
        self.assertTrue(personal_silent(p,'general'))

    @patch('telegram_checklist.enabled',return_value=False)
    def test_actual_sender_passes_silent_flag_without_suppressing_message(self,enabled):
        import io
        from platform_app import send_telegram
        for silent in (False,True):
            with patch('platform_app.urllib.request.urlopen',return_value=io.StringIO('{"ok":true,"result":{"message_id":42}}')) as send:
                self.assertEqual(send_telegram('test-token','-1','sample',silent=silent),42)
                self.assertIs(json.loads(send.call_args.args[0].data)['disable_notification'],silent)


class NotificationPreferencesTests(PostgresTestCase):
    setUp = fixtures.PersonalTelegramTests.setUp
    api = fixtures.PersonalTelegramTests.api
    register = fixtures.PersonalTelegramTests.register

    def body(self, revision=0):
        return dict(action='save',revision=revision,enabled={'forex.rsi':True},silent={'forex.rsi':True},quiet_hours=dict(enabled=True,start='22:00',end='07:00',timezone='Asia/Jerusalem'))

    def test_persistence_isolation_legacy_save_and_conflict(self):
        saved=self.api(OWNER,self.body())
        self.assertEqual(saved['silent'],{'forex.rsi':True})
        self.assertEqual(self.api(OWNER)['quiet_hours']['timezone'],'Asia/Jerusalem')
        self.assertEqual(self.api(OTHER)['silent'],{})
        self.assertFalse(self.api(OTHER)['quiet_hours']['enabled'])
        with self.assertRaises(psycopg.Error):self.api(OWNER,self.body())
        legacy=self.api(OWNER,dict(action='save',revision=1,enabled={'forex.rsi':False}))
        self.assertEqual(legacy['silent'],saved['silent'])
        self.assertEqual(legacy['quiet_hours'],saved['quiet_hours'])
        with self.assertRaises(psycopg.Error):self.api(STRANGER,self.body())

    def test_validation(self):
        bad=[('start','25:00'),('end','07:01:00'),('timezone','Invalid/Zone'),('enabled','true'),('start','07:00'),('timezone',None)]
        for key,value in bad:
            body=self.body();body['quiet_hours'][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(psycopg.Error):self.api(OWNER,body)
        for silent in [None,[],{'bogus':True},{'general':'true'}]:
            body=self.body();body['silent']=silent
            with self.subTest(silent=silent),self.assertRaises(psycopg.Error):self.api(OWNER,body)

    @patch('telegram_delivery.deliver',return_value='sent')
    def test_personal_sound_does_not_suppress_or_affect_other_user(self,deliver):
        self.register(OWNER,'-20',123);self.register(OTHER,'-30',456)
        body=self.body();body['quiet_hours']['enabled']=False;self.api(OWNER,body)
        deliver_personal(self.store,'token','text','event',Mock())
        calls={call.args[2]:call.kwargs['silent'] for call in deliver.call_args_list}
        self.assertEqual(calls,{'-20':True,'-30':False})
        deliver.reset_mock()
        deliver_personal(self.store,'token','text','event',Mock(),silent=True)
        self.assertTrue(all(c.kwargs['silent'] for c in deliver.call_args_list))

    def test_migration_rerun_preserves_preferences_and_wrappers(self):
        self.api(OWNER,self.body())
        with psycopg.connect(test_dsn()) as db:
            db.execute(Path('cloud/telegram-notification-sound.sql').read_text(),prepare=False)
        self.assertEqual(self.api(OWNER)['silent'],{'forex.rsi':True})
