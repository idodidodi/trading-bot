import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {detect,validateCandles} from './functions/backtests/engine.mjs';
const cases=JSON.parse(execFileSync('python3',['-c',`
import json
from dataclasses import asdict,replace
from test_scanner import fixture
from scanner import detect,read_csv,check_candles
from platform_app import load_rules
rules=load_rules();cases=[]
for bullish in (True,False):
 bars=fixture()
 if not bullish:bars=[replace(c,open=200-c.open,close=200-c.close,high=200-c.low,low=200-c.high) for c in bars]
 for provisional in (False,True):
  cases.append(dict(bars=[asdict(c) for c in bars],rules=rules,provisional=provisional,expected=detect(bars,rules,'TEST','4h',provisional=provisional)))
print(json.dumps(cases))
`],{encoding:'utf8'}));
for(const c of cases){const actual=detect(validateCandles(c.bars),c.rules,'TEST','4h',c.provisional);assert.equal(actual.length,c.expected.length);for(let i=0;i<actual.length;i++)for(const key of Object.keys(c.expected[i])){if(typeof c.expected[i][key]==='number')assert.ok(Math.abs(actual[i][key]-c.expected[i][key])<1e-9,key);else assert.deepEqual(actual[i][key],c.expected[i][key]);}}
assert.throws(()=>validateCandles([cases[0].bars[0],cases[0].bars[0]]),/Invalid/);
assert.throws(()=>validateCandles([{...cases[0].bars[0],close:NaN}]),/Invalid/);
console.log('Cloud replay matches Python: bullish/bearish, warm-up/confirmation, and malformed candle rejection.');
