import assert from 'node:assert/strict';
import fs from 'node:fs';
import {execFileSync} from 'node:child_process';
import {defaults} from './functions/csv-backtests/rules.mjs';
import {detectBBPinBars} from './functions/csv-backtests/bb-engine.mjs';
import {detectPinBars,parseCSV,replayCSV} from './functions/csv-backtests/engine.mjs';
// The marked EURUSD candle passes the relaxed 66% limit, including the bullish EMA override.
const marked={start:22*14400000,end:23*14400000,open:1.11885,high:1.119349,low:1.1175,close:1.11876};
const downtrend=Array.from({length:22},(_,i)=>({start:i*14400000,end:(i+1)*14400000,open:1.122-i*.0001,high:1.122-i*.0001,low:1.122-i*.0001,close:1.122-i*.0001}));
const markedSignals=detectPinBars([...downtrend,marked],defaults['pin-bars'],'test','EURUSD','4h');
assert.equal(markedSignals.at(-1).direction,'bullish');
const qualifiedSignals=detectPinBars([...downtrend,{...marked,high:1.119}],defaults['pin-bars'],'test','EURUSD','4h');
assert.equal(qualifiedSignals.at(-1).direction,'bullish');
assert.ok(qualifiedSignals.at(-1).fast_ema<qualifiedSignals.at(-1).slow_ema);
assert.ok(marked.close<qualifiedSignals.at(-1).slow_ema);
const start=1700000000000,bars=Array.from({length:70},(_,i)=>({start:start+i*14400000,open:100+i,high:101+i,low:99+i,close:100+i}));bars.at(-1).open=168;bars.at(-1).high=169.1;bars.at(-1).low=149;
const csv='time,open,high,low,close,EMA,EMA\n'+bars.map(b=>`${b.start},${b.open},${b.high},${b.low},${b.close},999,999`).join('\n');
const body={csv,symbol:'TEST',timeframe:'4h',strategy:'pin-bars'},now=bars.at(-1).start+14400000;
const result=await replayCSV(body,now);
assert.equal(result.signals.length,1);assert.equal(result.signals[0].confirmed_at,now);assert.equal(result.start_at,start);assert.equal(result.end_at,now);
assert.equal((await replayCSV(body,now-1)).excluded_open_candles,1);
const python=JSON.parse(execFileSync('python3',['-c','import sys,json;from csv_backtests import run;print(json.dumps(run(json.load(sys.stdin),'+now+')))'],{input:JSON.stringify(body),encoding:'utf8'}));
assert.deepEqual(result.signals,python.signals);assert.deepEqual(result.rules,python.rules);
for(const bad of ['time,open,high,low,close\n2026-01-01T00:00:00,1,2,0,1','time,open,high,low,close\n1700000000,1,0,2,1','time,open,high,low,close\n1700000000,1,2,0,1\n1700000000,1,2,0,1'])assert.throws(()=>parseCSV(bad,'4h',now));
assert.throws(()=>parseCSV('x'.repeat(2000001),'4h',now));
// The user's exported indicators are ignored: local and hosted replay use OHLC.
if(process.env.CSV_TEST_FILE){const real={csv:fs.readFileSync(process.env.CSV_TEST_FILE,'utf8'),symbol:'TVC:USOIL',timeframe:'4h',strategy:'pin-bars'},cutoff=Date.UTC(2026,9,12,12);const a=await replayCSV(real,cutoff);const b=JSON.parse(execFileSync('python3',['-c','import sys,json;from csv_backtests import run;print(json.dumps(run(json.load(sys.stdin),'+cutoff+')))'],{input:JSON.stringify(real),encoding:'utf8',maxBuffer:4000000}));assert.deepEqual(a.signals,b.signals);console.log('Attached USOIL CSV parity:',a.candles,'closed candles,',a.signal_count,'signals');}
console.log('CSV replay checks passed: local/cloud parity, dates, warm-up, open candles and invalid data.');
// BB Pin-Bars has its own identity and no EMA dependency; EMA results stay unchanged.
for(const mirror of [false,true]){
 const sample=Array.from({length:22},(_,i)=>({start:start+i*14400000,open:100,high:100,low:100,close:100}));
 sample.push({start:start+22*14400000,open:100,high:105,low:99.99,close:100.1});
 const bbBars=mirror?sample.map(b=>({...b,open:200-b.open,high:200-b.low,low:200-b.high,close:200-b.close})):sample;
 const input={...body,strategy:'bb-pin-bars',csv:'time,open,high,low,close\n'+bbBars.map(b=>`${b.start},${b.open},${b.high},${b.low},${b.close}`).join('\n')};
 const actual=await replayCSV(input,now);
 assert.equal(actual.signal_count,1);
 assert.equal(actual.signals[0].strategy,'bb-pin-bars');
 assert.equal(actual.signals[0].direction,mirror?'bullish':'bearish');
 assert.equal(actual.warmup_candles,19);
 assert.ok(!('fast_ema' in actual.signals[0]));
 const expected=JSON.parse(execFileSync('python3',['-c','import sys,json;from csv_backtests import run;print(json.dumps(run(json.load(sys.stdin),'+now+')))'],{input:JSON.stringify(input),encoding:'utf8'}));
 assert.deepEqual(actual.signals,expected.signals);
 const ema=await replayCSV({...input,strategy:'pin-bars'},now);
 assert.equal(ema.signal_count,mirror?1:0);
 assert.notEqual(ema.id,actual.id);
}
console.log('BB Pin-Bars checks passed: independent direction, reports, warm-up and local/cloud parity.');

// Mirrored shape boundaries and zero-body candles must match local Python replay.
for(const strategy of ['pin-bars','bb-pin-bars']){
 for(const mirror of [false,true]){
  for(const nose of [0,20,33,33.001]){
   for(const zeroBody of [false,true]){
    const sample=Array.from({length:22},(_,i)=>({start:start+i*14400000,open:149,high:149,low:149,close:149}));
    sample.push({start:start+22*14400000,open:150,high:(zeroBody?150:151)+nose,low:100,close:zeroBody?150:151});
    const candles=mirror?sample.map(b=>({...b,open:200-b.open,high:200-b.low,low:200-b.high,close:200-b.close})):sample;
    const input={...body,strategy,csv:'time,open,high,low,close\n'+candles.map(b=>`${b.start},${b.open},${b.high},${b.low},${b.close}`).join('\n')};
    const actual=await replayCSV(input,now);
    assert.equal(actual.signal_count,nose<=33?1:0,`${strategy}, mirror=${mirror}, nose=${nose}, zeroBody=${zeroBody}`);
    if(actual.signal_count)assert.equal(actual.signals[0].direction,mirror?'bearish':'bullish');
    const expected=JSON.parse(execFileSync('python3',['-c',`import sys,json;from csv_backtests import run;print(json.dumps(run(json.load(sys.stdin),${now})))`],{input:JSON.stringify(input),encoding:'utf8'}));
    assert.deepEqual(actual.signals,expected.signals);
    assert.deepEqual(actual.rules,expected.rules);
   }
  }
 }
}
for(const candle of [
 {open:158.1195,high:158.2095,low:158.0295,close:158.131},
 {open:158.5465,high:158.6005,low:158.469,close:158.5285},
]){
 const history=Array.from({length:22},(_,i)=>({start:start+i*14400000,end:start+(i+1)*14400000,open:candle.open,high:candle.open,low:candle.open,close:candle.open}));
 history.push({...candle,start:start+22*14400000,end:start+23*14400000});
 assert.equal(detectPinBars(history,defaults['pin-bars'],'test','USDJPY','4h').length,0);
 assert.equal(detectBBPinBars(history,defaults['bb-pin-bars'],'test','USDJPY','4h').length,0);
}
console.log('66% short-wick boundaries and USDJPY regressions passed for both strategies, with Python parity.');
