"""Offline checks for pin-bar photos and caption-aware dismissal."""
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch, Mock, MagicMock
from PIL import Image
from pinbar_chart import render, snapshot, notification_sender
from telegram_dismissal import DismissibleMessage, MessageCannotBeDeleted
from platform_app import send_telegram


def example():
    bars = [dict(start=i*3600000, open=100+i*.2, high=101+i*.2, low=99+i*.2,
                 close=100.5+i*.2) for i in range(24)]
    bars[-1].update(open=104.9, high=105.4, low=101.6, close=105.2)
    return dict(strategy='pin-bars', symbol='DEMO', timeframe='1h', direction='bullish',
                candle_at=bars[-1]['start'], confirmed_at=1795082400000,
                source='csv:demo:DEMO', chart_candles=bars,
                **{k: bars[-1][k] for k in ('open', 'high', 'low', 'close')})


class ChartTests(unittest.TestCase):
    def test_snapshot_excludes_future_and_limits_history(self):
        bars = [SimpleNamespace(start=i, open=1, high=2, low=0, close=1) for i in range(50)]
        saved = snapshot(bars, 30)
        self.assertEqual([b['start'] for b in saved], list(range(7, 31)))

    def test_render_and_legacy_single_candle(self):
        for direction in ('bullish', 'bearish'):
            s = example(); s['direction'] = direction
            for full in (True, False):
                if not full: s.pop('chart_candles')
                data = render(s)
                self.assertEqual(Image.open(io.BytesIO(data)).size, (1200, 720))
                self.assertEqual(data, render(s))

    def test_invalid_data_rejected(self):
        s = example(); s['chart_candles'][-1]['low'] = float('nan')
        with self.assertRaises(ValueError): render(s)
        s = example(); s['candle_at'] += 1
        with self.assertRaises(ValueError): render(s)

    def test_photo_caption_and_silent(self):
        from pin_bars import message
        s = example()
        with patch('platform_app.urllib.request.urlopen', return_value=io.StringIO('{"ok":true,"result":{"message_id":73}}')) as call, patch('telegram_checklist.enabled', return_value=False):
            self.assertEqual(send_telegram('token', '-123', message(s), silent=True, photo=render(s)), 73)
        request = call.call_args.args[0]
        self.assertTrue(request.full_url.endswith('/sendPhoto'))
        self.assertIn(b'name="caption"', request.data)
        self.assertIn(b'name="caption_entities"', request.data)
        self.assertIn(b'name="disable_notification"\r\n\r\ntrue', request.data)
        self.assertIn(b'\x89PNG', request.data)

    def test_sender_loads_persisted_snapshot(self):
        store = MagicMock()
        db = store.connect.return_value.__enter__.return_value
        db.execute.return_value.fetchone.return_value = (json.dumps(example()),)
        send = Mock()
        notification_sender(store, 'pin-bars:abc', send)('t', 'c', 'text', silent=True)
        self.assertTrue(send.call_args.kwargs['photo'].startswith(b'\x89PNG'))
        self.assertTrue(send.call_args.kwargs['silent'])
        self.assertIs(notification_sender(store, 'rsi-pb:abc', send), send)
        db.execute.return_value.fetchone.return_value = None
        with self.assertRaises(RuntimeError): notification_sender(store, 'pin-bars:missing', send)

    def test_suppressed_alert_does_not_render(self):
        store = MagicMock()
        db = store.connect.return_value.__enter__.return_value
        db.execute.return_value.fetchone.return_value = (json.dumps({'timeframe': '5m'}),)
        with patch('pinbar_chart.render') as draw:
            sender = notification_sender(store, 'pin-bars:suppressed', Mock())
        self.assertTrue(callable(sender))
        draw.assert_not_called()

    def test_photo_done_undo_and_delete_fallback(self):
        item = DismissibleMessage(-123, 7, dict(caption='original', caption_entities=[]), user_id=42)
        calls = []
        def call(method, body): calls.append((method, body))
        q = dict(id='q', message=dict(chat=dict(id=-123), message_id=7), **{'from': dict(id=42)})
        q['data'] = 'dismiss:done:' + item.key
        self.assertTrue(item.handle(q, call, clock=lambda: 10))
        self.assertEqual(calls[0][0], 'editMessageCaption')
        item = DismissibleMessage.restore(item.snapshot())
        q['data'] = 'dismiss:undo:' + item.key
        item.handle(q, call, clock=lambda: 11)
        self.assertEqual(calls[-2][1]['caption'], 'original')
        q['data'] = 'dismiss:done:' + item.key
        item.handle(q, call, clock=lambda: 12)
        def reject_delete(method, body):
            if method == 'deleteMessage': raise MessageCannotBeDeleted()
            call(method, body)
        item.expire(reject_delete, clock=lambda: 16)
        self.assertEqual(calls[-1][0], 'editMessageCaption')
        self.assertEqual(item.phase, 'completed')

from test_postgres_support import PostgresTestCase, TestStore


class ChartPersistenceTests(PostgresTestCase):
    def test_scan_persists_context_and_photo_delivery_deduplicates(self):
        import pin_bars
        from test_pin_bars import bars
        from telegram_delivery import deliver
        store = TestStore()
        candles = bars()
        asset = dict(id='X', provider='csv')
        rules = pin_bars.load_rules()
        pin_bars.scan(store, asset, '4h', candles[:-1], rules, candles[-2].end)
        pin_bars.scan(store, asset, '4h', candles, rules, candles[-1].end)
        with store.connect() as db:
            key, text = db.execute('SELECT id,text FROM notification_queue').fetchone()
            saved = json.loads(db.execute('SELECT payload FROM pin_bar_signals').fetchone()[0])
        self.assertEqual(len(saved['chart_candles']), 24)
        self.assertEqual(saved['chart_candles'][-1]['start'], candles[-1].start)
        send = notification_sender(store, key, send_telegram)
        with patch('telegram_checklist.enabled', return_value=False), patch('platform_app.urllib.request.urlopen',
                return_value=io.StringIO('{"ok":true,"result":{"message_id":73}}')) as call:
            self.assertEqual(deliver(store, 'token', '-123', text, key, send), 'sent')
            self.assertEqual(deliver(store, 'token', '-123', text, key, send), 'suppressed')
        call.assert_called_once()
        self.assertTrue(call.call_args.args[0].full_url.endswith('/sendPhoto'))

    def test_caption_controls_persist_and_restore(self):
        from telegram_personal import process
        store = TestStore()
        with patch('platform_app.Store', return_value=store), patch('platform_app.urllib.request.urlopen',
                return_value=io.StringIO('{"ok":true,"result":{"message_id":73,"chat":{"id":-123}}}')):
            send_telegram('token', '-123', 'Pin bar', dismissal_user_id=42, photo=render(example()))
        with store.connect() as db:
            key, state = db.execute('SELECT id,state FROM telegram_personal_messages').fetchone()
        self.assertIn('caption', json.loads(state)['original'])
        q = dict(id='q', message=dict(chat=dict(id=-123), message_id=73), **{'from': dict(id=42)})
        with patch('telegram_checklist.api') as api:
            q['data'] = 'dismiss:done:' + key
            process(store, 'token', q)
            q['data'] = 'dismiss:undo:' + key
            process(store, 'token', q)
        edits = [c for c in api.call_args_list if c.args[1] == 'editMessageCaption']
        self.assertEqual(len(edits), 2)
        self.assertEqual(edits[-1].args[2]['caption'], 'Pin bar')

if __name__ == '__main__': unittest.main()

