import json
import os
import unittest
import urllib.parse
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from scanner import Candle, fetch_twelve_data
from market_calendar import NY
from stock_rth import aggregate, next_close, session, STEP
from alpaca_feed import fetch_alpaca, fetch_alpaca_multi, fallback_asset

ASSET = dict(id='QCOM', symbol='QCOM', market='stock', provider='alpaca',
             feed_confirmed=True, exchange='NASDAQ')


def ms(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=NY).timestamp()*1000)


def bars(day, count=13):
    start = ms(day+'T09:30')
    return [Candle(start+i*STEP, start+(i+1)*STEP, 100+i, 102+i, 99+i, 101+i)
            for i in range(count)]


def payload(values):
    return dict(bars={'QCOM': [dict(t=datetime.fromtimestamp(b.start/1000, timezone.utc).isoformat(),
                                  o=b.open, h=b.high, l=b.low, c=b.close) for b in values]})


class RTHTests(unittest.TestCase):
    def test_normal_session_ohlc_and_extended_extremes_excluded(self):
        values = bars('2026-11-12')
        for at in ('07:00', '09:00', '16:00', '19:00'):
            start = ms('2026-11-12T'+at)
            values.append(Candle(start, start+STEP, 1, 999, 0, 2))
        result = aggregate(values, '4h', ms('2026-11-12T23:00'))
        self.assertEqual([(b.start,b.end) for b in result],
                         [(ms('2026-11-12T09:30'), ms('2026-11-12T13:30')),
                          (ms('2026-11-12T13:30'), ms('2026-11-12T16:00'))])
        self.assertEqual((result[0].open,result[0].high,result[0].low,result[0].close), (100,109,99,108))
        self.assertEqual((result[1].open,result[1].high,result[1].low,result[1].close), (108,114,107,113))

    def test_qcom_early_close_is_one_candle_and_no_postmarket_prices(self):
        result = aggregate(bars('2026-11-27'), '4h', ms('2026-11-27T20:00'))
        self.assertEqual(len(result),1)
        self.assertEqual((result[0].start,result[0].end), (ms('2026-11-27T09:30'),ms('2026-11-27T13:00')))
        self.assertEqual(result[0].close,107)
        self.assertEqual(result.sessions, [session(datetime(2026,11,27).date())])

    def test_hourly_final_half_hour(self):
        result=aggregate(bars('2026-11-12'),'1h',ms('2026-11-12T20:00'))
        self.assertEqual(len(result),7)
        self.assertEqual(result[-1].end-result[-1].start,STEP)

    def test_dst_and_holidays(self):
        for day,utc_hour in [('2026-03-06',14),('2026-03-09',13),('2026-10-30',13),('2026-11-02',14)]:
            result=aggregate(bars(day),'4h',ms(day+'T20:00'))
            self.assertEqual(datetime.fromtimestamp(result[0].start/1000,timezone.utc).hour,utc_hour)
            self.assertEqual(datetime.fromtimestamp(result[0].start/1000,NY).minute,30)
        for day in ('2026-11-26','2026-11-28'):
            self.assertEqual(aggregate(bars(day),'4h',ms(day+'T20:00')),[])

    def test_forming_candle_never_used_and_next_close_crosses_weekend(self):
        values=bars('2026-11-27')
        self.assertEqual(aggregate(values,'4h',ms('2026-11-27T12:59')),[])
        self.assertEqual(next_close('4h',ms('2026-11-27T13:00')),ms('2026-11-30T13:30'))
        self.assertEqual(next_close('1h',ms('2026-11-25T16:00')),ms('2026-11-27T10:30'))

    def test_missing_history_fails_closed_and_request_boundary_is_discarded(self):
        values=bars('2026-11-12')
        self.assertEqual(len(aggregate(values[1:],'4h',ms('2026-11-12T20:00'))),1)
        with self.assertRaisesRegex(ValueError,'missing half-hour'):
            aggregate(values[:10]+values[11:],'4h',ms('2026-11-12T20:00'))
        with self.assertRaisesRegex(ValueError,'missing intraday'):
            aggregate(values[:2]+values[4:],'1h',ms('2026-11-12T20:00'))

    def test_intraday_request_pagination_and_feed_isolation(self):
        values=bars('2026-11-27',7)
        first=payload(values[4:]);first['next_page_token']='next'
        with patch('alpaca_feed.request_page',side_effect=[first,payload(values[:4])]) as req:
            result=fetch_alpaca(ASSET,'4h',end='2026-11-28T00:00:00Z')
        self.assertEqual(req.call_count,2)
        self.assertEqual(req.call_args.args[0]['timeframe'],'30Min')
        self.assertEqual(len(result),1)
        self.assertTrue(fallback_asset(ASSET,'4h')['exchange'].endswith('rth-open-v1'))
        self.assertEqual(fallback_asset(ASSET,'daily')['exchange'],'Alpaca-sip-split')
        self.assertEqual(ASSET['exchange'],'NASDAQ')

    def test_live_pagination_counts_rth_candles_not_source_rows(self):
        from market_calendar import stock_trading_day, stock_close
        values=[]
        day=datetime(2025,1,2).date()
        while len(values)<3500:
            if stock_trading_day(day):
                opening,closing=session(day)
                values.extend(Candle(t,t+STEP,100,102,99,101) for t in range(opening,closing,STEP))
            day+=timedelta(days=1)
        values.reverse()
        pages=[]
        for i in range(0,len(values),500):
            page=payload(values[i:i+500])
            if i+500<len(values):page['next_page_token']=str(i+500)
            pages.append(page)
        with patch('alpaca_feed.request_page',side_effect=pages) as request:
            result=fetch_alpaca(ASSET,'4h',end='2026-11-28T00:00:00Z')
        self.assertGreater(request.call_count,1)
        self.assertEqual(len(result),500)
        self.assertEqual(result[-1].end,values[0].end)

    def test_multi_and_report_paths_use_same_rth_bars(self):
        from stock_report_data import fetch_batch
        values=bars('2026-11-27',7)
        cutoff=ms('2026-11-27T20:00')
        with patch('alpaca_feed.request_page',return_value=payload(values)) as req:
            multi=fetch_alpaca_multi(['QCOM'],'4h',end=cutoff)
            report=fetch_batch(['QCOM'],'4h',cutoff,250,'sip')
        self.assertEqual(multi['QCOM'],report['QCOM'])
        self.assertEqual(len(report['QCOM']),1)
        self.assertTrue(all(c.args[0]['timeframe']=='30Min' for c in req.call_args_list))

    def test_new_watch_keeps_extended_hours_and_clock_alignment(self):
        from stock_rsi_data import aggregate as watch_aggregate
        values=[]
        for hour in (4,8,12,16):
            start=ms(f'2026-11-25T{hour:02d}:00')
            values.append(Candle(start,start+15*60000,100,110,90,105))
        result=watch_aggregate(values,'4h',ms('2026-11-25T20:00'))
        self.assertEqual([datetime.fromtimestamp(b.start/1000,NY).hour for b in result],[4,8,12,16])

    def test_schedule_uses_actual_next_rth_close_and_delay(self):
        from scan_schedule import next_due
        now=ms('2026-11-25T16:16')
        result=aggregate(bars('2026-11-25'),'4h',now-16*60000)
        self.assertEqual(next_due(fallback_asset(ASSET,'4h'),'4h',result,result,now,{'status':'scanned'}),
                         ms('2026-11-27T13:16'))

    def test_twelve_explicit_rth_and_early_close(self):
        import io
        response=dict(meta={'interval':'4h','symbol':'QCOM','exchange':'NASDAQ'},
                      values=[dict(datetime='2026-11-27 14:30:00',open='100',high='110',low='90',close='105')])
        with patch.dict(os.environ,{'TWELVE_DATA_API_KEY':'test'}), patch('scanner.urllib.request.urlopen',return_value=io.StringIO(json.dumps(response))) as req:
            result=fetch_twelve_data(ASSET|{'provider':'twelvedata'},'4h')
        self.assertEqual(result[0].end,ms('2026-11-27T13:00'))
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(req.call_args.args[0]).query)['prepost'],['false'])
        response['values'][0]['datetime']='2026-11-27 12:00:00'
        with patch.dict(os.environ,{'TWELVE_DATA_API_KEY':'test'}), patch('scanner.urllib.request.urlopen',return_value=io.StringIO(json.dumps(response))):
            with self.assertRaisesRegex(ValueError,'RTH opening'):
                fetch_twelve_data(ASSET|{'provider':'twelvedata'},'4h')


from test_postgres_support import PostgresTestCase, TestStore


class RTHScannerTests(PostgresTestCase):
    def test_old_state_and_cache_cannot_pollute_new_baseline(self):
        from dataclasses import replace
        from test_scanner import fixture
        from scanner import scan_once, prepare_store
        from platform_app import load_rules
        from managed_assets import cache_candles
        rules=load_rules()
        store=TestStore();prepare_store(store)
        asset=ASSET | dict(enabled=True, timeframes=['4h'], scan_strategies=['rsi','rsi-pb'])
        old=fallback_asset(asset)
        legacy='alpaca:Alpaca-sip-split:QCOM'
        values=fixture()
        delta=ms('2026-11-25T13:30')-values[-1].end
        values=[replace(b,start=b.start+delta,end=b.end+delta) for b in values]
        cache_candles(store,old,'4h',values)
        with store.connect() as db:
            db.execute('INSERT INTO scan_state(asset,timeframe,last_end) VALUES(%s,%s,%s)',
                       (f"QCOM@{legacy}@{rules['skill_hash']}@all-stages-v2",'4h',values[0].end))
        with patch('alpaca_feed.fetch_alpaca',return_value=values):
            rows=scan_once(store,dict(assets=[asset],timeframes=['4h']),rules,ms('2026-11-25T13:46'))
        self.assertEqual(rows[0]['status'],'baseline set')
        self.assertEqual(rows[0]['rsi_pb']['status'],'baseline set')
        self.assertEqual(rows[0]['new_signals'],0)
        self.assertEqual(store.rows(),[])
        with store.connect() as db:
            feeds={r[0] for r in db.execute('SELECT DISTINCT feed FROM candle_cache')}
        self.assertEqual(feeds,{legacy,'alpaca:Alpaca-sip-split-rth-open-v1:QCOM'})


if __name__ == '__main__':
    unittest.main()
