import assert from 'node:assert/strict';
import fs from 'node:fs';
import {execFileSync} from 'node:child_process';
import {detectPinBars,parseCSV,replayCSV} from './functions/csv-backtests/engine.mjs';
// The marked red-bodied EURUSD candle is bullish even below EMA 21 in a downtrend.
const marked={start:22*14400000,end:23*14400000,open:1.11885,high:1.119349,low:1.1175,close:1.11876};
const downtrend=Array.from({length:22},(_,i)=>({start:i*14400000,end:(i+1)*14400000,open:1.122-i*.0001,high:1.122-i*.0001,low:1.122-i*.0001,close:1.122-i*.0001}));
const markedSignals=detectPinBars([...downtrend,marked],{fast:8,slow:21,ratio:2},'test','EURUSD','4h');
assert.equal(markedSignals.at(-1).direction,'bullish');
assert.ok(markedSignals.at(-1).fast_ema<markedSignals.at(-1).slow_ema);
assert.ok(marked.close<markedSignals.at(-1).slow_ema);
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
