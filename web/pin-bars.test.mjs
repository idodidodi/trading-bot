import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {test} from 'node:test';

test('Pin-Bars strategy selection renders independent evidence, coverage and pages',async()=>{
 class Element {
  constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.textContent='';}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  setAttribute(){}
  addEventListener(){}
 }
 const elements=new Map();
 const get=id=>{if(!elements.has(id))elements.set(id,new Element('div'));return elements.get(id);};
 const requested=[];
 const signal={symbol:'EURUSD',source:'csv:configured:EURUSD',timeframe:'4h',direction:'bullish',candle_at:1700000000000,confirmed_at:1700014400000,open:110,high:111.1,low:100,close:111,fast_ema:108,slow_ema:105,body:1,lower_wick:10,upper_wick:.1,rules:{seed:'first close'}};
 const data={rules:{fast:8,slow:21,ratio:2,max_short_wick_ratio:0.66},signals:[signal],scanned_at:signal.confirmed_at,has_more:true,coverage:[{asset:'EURUSD',timeframe:'4h',provider:'csv',status:'scanned',last_closed_at:signal.confirmed_at,candles:250,new_signals:1}]};
 const combined={...data,signals:[{...signal,strategy:'rsi-pb',version:'test-v1',pinbar_direction:'bearish',pivot2:1699985600000,pivot_closed_at:1700000000000,divergences:[{pivot1:1699900000000,pivot1_closed_at:1699914400000,price1:100,price2:90,rsi1:20,rsi2:30,band2:91,band_touch_price:89}]}],coverage:[{...data.coverage[0],waiting_setups:2}]};
 const context=vm.createContext({window:{},document:{getElementById:get,createElement:tag=>new Element(tag)},sessionStorage:{getItem:()=>null},location:{pathname:'/logs'},fetch:async url=>{requested.push(url);return {ok:true,json:async()=>url.startsWith('/api/rsi-pb')?combined:url.startsWith('/api/bb-pin-bars')?{...data,rules:{bb_period:20,bb_multiplier:2,ratio:2,max_short_wick_ratio:0.66},signals:[{...signal,strategy:'bb-pin-bars',lower_bb:101,upper_bb:115}]}:url.startsWith('/api/pin-bars')?data:{rows:[]}};},URLSearchParams,AbortController,setTimeout,clearTimeout,Intl,Node:Element});
 vm.runInContext(fs.readFileSync(new URL('./app.js',import.meta.url),'utf8'),context);
 await new Promise(resolve=>setImmediate(resolve));
 await vm.runInContext("strategyViews.live='pin-bars';findings('live')",context);
 const text=n=>[n.textContent,...n.children.map(c=>c instanceof Element?text(c):String(c))].join(' ');
 assert.match(text(get('content')),/Pin-Bars signals/);
 assert.match(text(get('content')),/OHLC: 110 \/ 111.1 \/ 100 \/ 111/);
 assert.match(text(get('content')),/Pin-Bars coverage/);
 assert.match(text(get('content')),/short wick ≤ 66% of long wick/);
 const descendants=n=>[n,...n.children.filter(c=>c instanceof Element).flatMap(descendants)];
 const all=descendants(get('content'));
 const strategy=all.find(n=>n.tag==='select');
 assert.ok(strategy.children.some(n=>n.value==='pin-bars'));
 assert.equal(vm.runInContext("strategySelector('backtest').children[0].children.some(n=>n.value==='pin-bars')",context),true);
 all.find(n=>n.tag==='button'&&n.textContent==='Next').onclick();
 await new Promise(resolve=>setImmediate(resolve));
 assert.ok(requested.includes('/api/pin-bars?page=1'));
 await vm.runInContext("strategyViews.live='bb-pin-bars';page=0;findings('live')",context);
 assert.match(text(get('content')),/BB Pin-Bars signals/);
 assert.match(text(get('content')),/short wick ≤ 66% of long wick/);
 assert.match(text(get('content')),/Lower BB: 101 · upper BB: 115/);
 assert.doesNotMatch(text(get('content')),/Fast EMA:/);
 assert.ok(requested.includes('/api/bb-pin-bars?page=0'));
 descendants(get('content')).find(n=>n.tag==='button'&&n.textContent==='Next').onclick();
 await new Promise(resolve=>setImmediate(resolve));
 assert.ok(requested.includes('/api/bb-pin-bars?page=1'));
 assert.equal(vm.runInContext("strategySelector('backtest').children[0].children.some(n=>n.value==='bb-pin-bars')",context),true);
 await vm.runInContext("strategyViews.live='rsi-pb';page=0;findings('live')",context);
 assert.match(text(get('content')),/RSI \+ PB signals/);
 assert.match(text(get('content')),/RSI Divergence and PinBars/);
 assert.match(text(get('content')),/Opposite pinbar: bearish/);
 assert.match(text(get('content')),/RSI 20.00 → 30.00/);
 assert.match(text(get('content')),/2 waiting setups/);
 assert.doesNotMatch(text(get('content')),/Fast EMA:/);
 assert.ok(requested.includes('/api/rsi-pb?page=0'));
 descendants(get('content')).find(n=>n.tag==='button'&&n.textContent==='Next').onclick();
 await new Promise(resolve=>setImmediate(resolve));
 assert.ok(requested.includes('/api/rsi-pb?page=1'));
 assert.equal(vm.runInContext("strategySelector('backtest').children[0].children.some(n=>n.value==='rsi-pb')",context),true);
 strategy.value='rsi';
 // A stale Pin-Bars response must not overwrite another strategy's view.
 vm.runInContext("strategyViews.live='rsi'",context);
 await vm.runInContext('pinBarFindings()',context);
 assert.doesNotMatch(text(get('content')),/Pin-Bars signals/);
});
