import io
import json
from dataclasses import replace
from datetime import datetime, timezone, date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from platform_app import Store,load_rules,quiet_hours,send_telegram
from scanner import scan_once,prepare_store,Candle
from scanner_control import request,claim,finish,status
from market_calendar import stock_candle_end,stock_trading_day,next_market_time
from test_scanner import fixture


def ms(value):
    return int(datetime.fromisoformat(value).timestamp()*1000)


class FamilyUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'state.db'
        self.store=Store(self.path);prepare_store(self.store)

    def test_weekly_and_monthly_reads_survive_restart_including_insufficient_history(self):
        for tf,step,start,now in [('weekly',7,'2025-01-06T00:00:00+00:00','2025-10-21T00:00:00+00:00'),
                                  ('monthly',30,'2022-01-01T00:00:00+00:00','2025-05-20T00:00:00+00:00')]:
            from scanner import interval_end
            dt=datetime.fromisoformat(start);bars=[]
            for _ in range(40):
                end=interval_end(dt,tf);bars.append(Candle(int(dt.timestamp()*1000),int(end.timestamp()*1000),100,101,99,100));dt=end
            cfg=dict(assets=[dict(id=tf,provider='twelvedata',symbol=tf)],timeframes=[tf])
            with patch('scanner.fetch_twelve_data',return_value=bars) as feed:
                first=scan_once(self.store,cfg,load_rules(),ms(now))
                second=scan_once(Store(self.path),cfg,load_rules(),ms(now)+3600000)
                self.assertEqual(feed.call_count,1)
                self.assertEqual(first[0]['status'],'insufficient history')
                self.assertEqual(second[0]['check_state'],'waiting for candle close')

    def test_catchup_saves_missed_findings_without_individual_alerts_and_one_summary(self):
        bars=fixture();rules=load_rules()|dict(max_band_slope_pct=1.5)
        cfg=dict(assets=[dict(id='BTCUSD',market='crypto',provider='twelvedata',symbol='BTC/USD')],timeframes=['4h'])
        with patch('scanner.fetch_twelve_data',return_value=bars[:256]):
            scan_once(self.store,cfg,rules,bars[255].end)
        command=request(self.store);self.assertEqual(request(self.store)['id'],command['id'])
        self.assertEqual(claim(self.store),command['id'])
        with patch('scanner.fetch_twelve_data',return_value=bars):
            coverage=scan_once(self.store,cfg,rules,bars[-1].end,catchup=command['id'])
        # A restart can resume from saved coverage with zero newly inserted rows.
        finish(Store(self.path),command['id'],coverage,0)
        self.assertEqual(status(self.store)['findings'],2)
        self.assertTrue(all(row[1]=='summarized' for row in self.store.rows()))
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM notification_queue').fetchone()[0],1)
        # Another scan of the same closed candle doesn't refetch or resubmit signals.
        with patch('scanner.fetch_twelve_data') as feed:
            scan_once(Store(self.path),cfg,rules,bars[-1].end+1000,catchup=True)
            feed.assert_not_called()

    def test_silent_summary_boundaries_in_israel(self):
        for clock,silent in [('23:29',False),('23:30',True),('00:00',True),('06:29',True),('06:30',False)]:
            self.assertEqual(quiet_hours(ms('2026-10-09T'+clock+':00+03:00')/1000),silent)
        with patch('platform_app.urllib.request.urlopen',return_value=io.StringIO('{"ok":true}')) as post:
            send_telegram('test','123','Catch-up',silent=True)
            self.assertTrue(json.loads(post.call_args.args[0].data)['disable_notification'])

    def test_stock_week_month_closes_holidays_early_close_and_dst(self):
        self.assertFalse(stock_trading_day(date(2026,12,25)))
        end=stock_candle_end(datetime.fromisoformat('2026-12-21T05:00:00+00:00'),'weekly')
        self.assertEqual(end.isoformat(),'2026-12-24T13:00:00-05:00')
        end=stock_candle_end(datetime.fromisoformat('2026-05-01T04:00:00+00:00'),'monthly')
        self.assertEqual(end.isoformat(),'2026-05-29T16:00:00-04:00')
        self.assertEqual(next_market_time(dict(market='forex'),ms('2026-10-10T12:00:00-04:00')),ms('2026-10-11T17:00:00-04:00'))

    def test_selected_stock_immediately_reviews_latest_closed_week(self):
        from scanner import detect
        bars=fixture();rules=load_rules()|dict(max_band_slope_pct=1.5)
        # The latest completed candle contains this confirmation.
        bars=bars[:258]
        a=dict(id='STOCK-TEST',symbol='TEST',provider='alpaca',market='stock',exchange='NASDAQ',feed_confirmed=True,
               weekly_screen_selected=True,weekly_screen_since=0,weekly_screen_week_start=0)
        with patch('alpaca_feed.fetch_alpaca',return_value=bars):
            scan_once(self.store,dict(assets=[a],timeframes=['weekly']),rules,bars[-1].end)
        signals=[json.loads(r[0]) for r in self.store.rows()]
        self.assertTrue(any(s.get('signal_status')!='provisional' for s in signals))

    def test_daily_selection_excludes_usdcny_even_without_portfolio_entry(self):
        from daily_universe import rank
        pairs={'CN':dict(status='online',wsname='USD/CNY'),'EU':dict(status='online',wsname='EUR/USD')}
        tickers={k:dict(v=['0','100'],p=['0','1'],c=['1']) for k in pairs}
        ranked=rank(pairs,tickers,dict(forex={'USD/CNY','EUR/USD'},crypto=set()),set(),5)
        self.assertNotIn('USD/CNY',[r['symbol'] for r in ranked['forex']])


class WindowsUpdateTests(unittest.TestCase):
    def test_update_rejects_downgrades_untrusted_urls_and_checksum_mismatch(self):
        import windows_update as u
        self.assertLess(u.version_parts('v1.0.9'),u.version_parts('v1.1.0'))
        with self.assertRaises(ValueError):u.version_parts('v1.2.3;command')
        with self.assertRaises(ValueError):u.release_url('https://evil.invalid/app.exe','v1.1.0','FamilyTradingBot.exe')
        release=dict(tag_name='v1.1.0',assets=[dict(name=name,size=4,browser_download_url=f'https://github.com/{u.REPOSITORY}/releases/download/v1.1.0/{name}') for name in ['FamilyTradingBot.exe','windows-update.json']])
        class Response(io.BytesIO):
            def __enter__(self):return self
            def __exit__(self,*args):self.close()
        with tempfile.TemporaryDirectory() as root,patch('windows_update.get_json',return_value=dict(version='1.1.0',size=4,sha256='0'*64)),patch('windows_update.urllib.request.urlopen',return_value=Response(b'MZxx')):
            with self.assertRaisesRegex(ValueError,'checksum'):u.download_release(Path(root),release)
            self.assertFalse((Path(root)/'FamilyTradingBot.new.exe').exists())


if __name__=='__main__':unittest.main()
