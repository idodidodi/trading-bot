import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {test} from 'node:test';
test('signal candle uses saved OHLC proportions, body color and a flat doji',()=>{
 class Element{constructor(tag){this.tag=tag;this.children=[];this.attrs={};}append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}setAttribute(k,v){this.attrs[k]=v;}addEventListener(){}}
 const context=vm.createContext({window:{},document:{getElementById:()=>new Element('div'),createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag)},sessionStorage:{getItem:()=>null},location:{pathname:'/logs'},fetch:async()=>({ok:true,json:async()=>({rows:[]})}),URLSearchParams,AbortController,setTimeout,clearTimeout,Intl,Node:Element});
 vm.runInContext(fs.readFileSync(new URL('./app.js',import.meta.url),'utf8'),context);
 const bullish=vm.runInContext('candleDrawing({open:108,high:111,low:100,close:110})',context),wick=bullish.children[1],body=bullish.children[2];
 assert.equal(wick.attrs.y1,'10');assert.equal(wick.attrs.y2,'86');assert.equal(body.attrs.fill,'#34d399');assert.ok(Math.abs(Number(body.attrs.height)-76*2/11)<1e-10);assert.match(bullish.attrs['aria-label'],/open 108, high 111, low 100, close 110/);
 assert.equal(vm.runInContext('candleDrawing({open:110,high:111,low:100,close:108}).children[2].attrs.fill',context),'#f87171');
 const doji=vm.runInContext('candleDrawing({open:108,high:111,low:100,close:108})',context);assert.equal(doji.children[2].tag,'line');assert.equal(doji.children[2].attrs.y1,doji.children[2].attrs.y2);
 assert.equal(vm.runInContext('candleDrawing({open:10,high:10,low:10,close:10}).children[1].attrs.y1',context),'48');
 assert.equal(vm.runInContext('candleDrawing({price2:10}).textContent',context),'OHLC unavailable');
});

for(const hosted of [false,true])test(`${hosted?'hosted':'local'} inspection opens immediately and shows API failures inside the dialog`,async()=>{
 class Element{constructor(){this.children=[];this.open=false;}append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}setAttribute(){}addEventListener(){}showModal(){this.open=true;}}
 const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
 const context=vm.createContext({window:{DASHBOARD_CONFIG:hosted?{supabaseUrl:'https://example.test',publishableKey:'public',installationId:'test'}:{}},document:{getElementById:get,createElement:()=>new Element(),querySelectorAll:()=>[]},sessionStorage:{getItem:()=>null,setItem(){}},location:{pathname:'/logs'},fetch:async()=>({ok:true,json:async()=>({rows:[]})}),URLSearchParams,AbortController,setTimeout,clearTimeout,Intl,Node:Element});
 vm.runInContext(fs.readFileSync(new URL('./app.js',import.meta.url),'utf8'),context);
 let fail;context.fetch=()=>new Promise((_,reject)=>{fail=reject;});
 const pending=vm.runInContext("openCandle('live','finding')",context);
 assert.equal(get('candle-dialog').open,true);
 assert.equal(get('candle-note').textContent,'Loading candles…');
 await new Promise(resolve=>setImmediate(resolve));
 fail(Object.assign(new Error('aborted'),{name:'AbortError'}));await pending;
 assert.match(get('candle-note').textContent,/timed out/);
 assert.equal(get('status').textContent,get('candle-note').textContent);
 context.fetch=async()=>({ok:false,json:async()=>({message:'Finding not found'})});
 await vm.runInContext("openCandle('live','missing')",context);
 assert.match(get('candle-note').textContent,/Could not load candles: Finding not found/);
 context.fetch=async()=>({ok:true,json:async()=>({signal:{symbol:'TEST',timeframe:'daily',rules:{bb_period:20,bb_multiplier:2,rsi_period:14}},candles:[],tradingview_url:'https://example.test/chart'})});
 await vm.runInContext("openCandle('backtest','saved')",context);
 assert.match(get('candle-title').textContent,/TEST · Daily candles/);
 assert.match(get('candle-note').textContent,/No stored source candles/);
});
