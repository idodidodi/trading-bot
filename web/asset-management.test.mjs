import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {test} from 'node:test';

class Element {
 constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.attrs={};this.textContent='';this.listeners={};this.classList={add(){},toggle(){}};}
 append(...children){this.children.push(...children);if(this.tag==='select'&&this.value===undefined)this.value=children[0]?.value||'';}
 prepend(...children){this.children.unshift(...children);}
 replaceChildren(...children){this.children=children;}
 setAttribute(k,v){this.attrs[k]=v;}
 addEventListener(k,fn){this.listeners[k]=fn;}
 showModal(){this.open=true;}
 close(){this.open=false;this.listeners.close?.();}
 querySelectorAll(tag){return descendants(this).slice(1).filter(e=>e.tag===tag);}
}
const descendants=e=>[e,...e.children.filter(c=>c instanceof Element).flatMap(descendants)];
const text=e=>[e.textContent,...e.children.map(c=>c instanceof Element?text(c):String(c))].join(' ');
function setup(settings,saveError=null){
 const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,new Element('div'));return elements.get(id);};
 const requests=[];
 const context=vm.createContext({window:{},document:{getElementById:get,createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag)},sessionStorage:{getItem:()=>null},location:{pathname:'/logs'},fetch:async(url,options)=>{
  const body=options.body?JSON.parse(options.body):null;requests.push({url,body});
  if(url==='/api/assets'){
   if(body?.action==='bulk'){settings={...settings,revision:settings.revision+1,config:{...settings.config,assets:[...settings.config.assets,{id:body.tickers,provider:'csv',enabled:false,timeframes:['daily']}]}};}
   if(body?.action==='save'&&saveError)return {ok:false,json:async()=>({error:saveError})};
   return {ok:true,json:async()=>structuredClone(settings)};
  }
  return {ok:true,json:async()=>({rows:[]})};
 },URLSearchParams,AbortController,setTimeout,clearTimeout,Intl,Node:Element});
 vm.runInContext(fs.readFileSync(new URL('./app.js',import.meta.url),'utf8'),context);
 return {context,get,requests};
}
const asset={id:'BTCUSD',provider:'twelvedata',symbol:'BTC/USD',exchange:'Binance',market:'crypto',feed_confirmed:true,enabled:true,timeframes:['monthly','4h'],timeframe_providers:{'4h':'kraken'},scan_strategies:['rsi','bb-pin-bars'],tradingview_symbol:'BINANCE:BTCUSDT',path:'keep.csv'};
const settings={revision:17,config:{assets:[asset],timeframes:['weekly']},coverage:[]};
const formFor=get=>descendants(get('asset-content')).find(e=>e.tag==='form');
const submit=form=>form.onsubmit({preventDefault(){}});

test('provider identities and overrides map to exact managed assets; ambiguous symbols stay ambiguous',()=>{
 const {context}=setup(settings);
 assert.equal(vm.runInContext("assetMatches({symbol:'kraken:Kraken:BTC/USD'},"+JSON.stringify([asset])+")[0].id",context),'BTCUSD');
 const stock={id:'STOCK-AAPL',provider:'alpaca',symbol:'AAPL',exchange:'NASDAQ',market:'stock'};
 assert.equal(vm.runInContext("assetMatches({symbol:'alpaca:Alpaca-sip-split:AAPL'},"+JSON.stringify([stock])+")[0].id",context),'STOCK-AAPL');
 const fx={id:'EURUSD',provider:'oanda',symbol:'EUR/USD',exchange:'OANDA'};
 assert.equal(vm.runInContext("assetMatches({symbol:'oanda:OANDA-practice:EUR/USD'},"+JSON.stringify([fx])+")[0].id",context),'EURUSD');
 const dax={id:'DAX',provider:'yahoo',symbol:'^GDAXI',exchange:'XETRA',market:'index'};
 assert.equal(vm.runInContext("assetMatches({symbol:'yahoo:XETRA:^GDAXI'},"+JSON.stringify([dax])+")[0].id",context),'DAX');
 assert.equal(vm.runInContext("assetMatches({symbol:'BTC/USD'},"+JSON.stringify([asset,{...asset,id:'OTHER'}])+").length",context),2);
});

test('modal replaces/adds/removes timeframes, disables asset and preserves feed settings without navigating',async()=>{
 const {context,get,requests}=setup(settings);
 await vm.runInContext("openAssetManager({symbol:'BTCUSD',timeframe:'4h'})",context);
 assert.equal(get('asset-dialog').open,true);
 const form=formFor(get),inputs=descendants(form).filter(e=>e.tag==='input');
 inputs[0].checked=false;
 for(const input of inputs.filter(i=>['monthly','weekly','daily','4h','1h'].includes(i.value)))input.checked=['daily','1h'].includes(input.value);
 await submit(form);
 const saved=requests.find(r=>r.body?.action==='save').body;
 assert.equal(saved.revision,17);assert.equal(saved.asset.enabled,false);
 assert.deepEqual(saved.asset.timeframes,['daily','1h']);
 assert.deepEqual(saved.asset.timeframe_providers,asset.timeframe_providers);
 assert.deepEqual(saved.asset.scan_strategies,asset.scan_strategies);
 assert.equal(saved.asset.tradingview_symbol,asset.tradingview_symbol);
 assert.equal(get('asset-dialog').open,false);assert.match(text(get('status')),/scanner disabled/);
});

test('empty timeframe selection stays in modal and sends no mutation; cancel sends no mutation',async()=>{
 const {context,get,requests}=setup(settings);
 await vm.runInContext("openAssetManager({symbol:'BTCUSD'})",context);
 const form=formFor(get);
 for(const input of descendants(form).filter(e=>e.tag==='input'&&e.value))input.checked=false;
 await submit(form);assert.match(text(form),/Choose at least one timeframe/);
 assert.equal(get('asset-dialog').open,true);assert.ok(!requests.some(r=>r.body));
 descendants(form).find(e=>e.textContent==='Cancel').onclick();
 assert.equal(get('asset-dialog').open,false);assert.ok(!requests.some(r=>r.body));
});

test('strategy selections start from saved settings and save added and removed strategies with the timeframes',async()=>{
 const {context,get,requests}=setup(settings);
 await vm.runInContext("openAssetManager({symbol:'BTCUSD'})",context);
 const form=formFor(get),strategies=descendants(form).filter(e=>e.tag==='input'&&['rsi','pin-bars','bb-pin-bars'].includes(e.value));
 assert.deepEqual(strategies.filter(i=>i.checked).map(i=>i.value),['rsi','bb-pin-bars']);
 for(const input of strategies)input.checked=['pin-bars','bb-pin-bars'].includes(input.value);
 await submit(form);
 const saved=requests.find(r=>r.body?.action==='save').body;
 assert.deepEqual(saved.asset.scan_strategies,['pin-bars','bb-pin-bars']);
 assert.deepEqual(saved.asset.timeframes,asset.timeframes);
 assert.deepEqual(saved.asset.timeframe_providers,asset.timeframe_providers);
 assert.equal(saved.asset.enabled,true);
});

test('legacy assets show default strategies; empty strategy selection sends no mutation',async()=>{
 const legacy={...asset};delete legacy.scan_strategies;
 const {context,get,requests}=setup({...settings,config:{...settings.config,assets:[legacy]}});
 await vm.runInContext("openAssetManager({symbol:'BTCUSD'})",context);
 const form=formFor(get),strategies=descendants(form).filter(e=>e.tag==='input'&&['rsi','pin-bars','bb-pin-bars'].includes(e.value));
 assert.deepEqual(strategies.filter(i=>i.checked).map(i=>i.value),['rsi','pin-bars']);
 for(const input of strategies)input.checked=false;
 await submit(form);
 assert.match(text(form),/Choose at least one scan strategy/);
 assert.equal(get('asset-dialog').open,true);assert.ok(!requests.some(r=>r.body));
});

test('revision conflicts preserve edited controls and show the error inside the dialog',async()=>{
 const {context,get}=setup(settings,'Assets changed in another session. Reload before saving.');
 await vm.runInContext("openAssetManager({symbol:'BTCUSD'})",context);
 const form=formFor(get);descendants(form).find(e=>e.tag==='input').checked=false;
 await submit(form);assert.equal(get('asset-dialog').open,true);
 assert.match(text(form),/Assets changed in another session/);
 assert.equal(descendants(form).find(e=>e.tag==='input').checked,false);
 assert.equal(descendants(form).find(e=>e.textContent==='Save changes').disabled,false);
});

test('rotating weekly stock uses observed feed, persists market and reports a disabled draft on failed registration save',async()=>{
 const current={revision:1,config:{assets:[]},coverage:[{asset:'STOCK-AAPL',provider:'alpaca',symbol:'AAPL',exchange:'Alpaca-sip-split',timeframe:'weekly'}]};
 const {context,get,requests}=setup(current,'Unavailable feed');
 await vm.runInContext("openAssetManager({symbol:'alpaca:Alpaca-sip-split:AAPL'})",context);
 const form=formFor(get);assert.match(text(form),/permanent assets/);
 await submit(form);
 assert.equal(requests.find(r=>r.body?.action==='bulk').body.tickers,'STOCK-AAPL');
 const saved=requests.find(r=>r.body?.action==='save').body;
 assert.equal(saved.revision,2);assert.equal(saved.asset.market,'stock');assert.equal(saved.asset.provider,'alpaca');
 assert.match(text(form),/disabled draft was added/);
});

test('unmapped historical assets provide feed configuration instead of guessing a scanner asset',async()=>{
 const {context,get,requests}=setup(settings);
 await vm.runInContext("openAssetManager({symbol:'UNKNOWN'})",context);
 assert.match(text(get('asset-content')),/exact data feed/);assert.ok(!requests.some(r=>r.body));
});

test('rendered rows across all strategies and historical results open the asset manager',async()=>{
 const {context,get}=setup(settings);
 const base={symbol:'BTCUSD',timeframe:'4h',direction:'bullish',confirmed_at:1700014400000,candle_at:1700000000000,open:110,high:111,low:100,close:111,body:1,lower_wick:10,upper_wick:0,rules:{seed:'close'},source:'twelvedata:Binance:BTC/USD',strategy:'pin-bars',price1:100,price2:110,rsi1:30,rsi2:40};
 context.result={findings:[{id:'one',signal:base}],summary:[]};
 context.pinData={rules:{fast:8,slow:21,ratio:2,bb_period:20,bb_multiplier:2},signals:[base],coverage:[]};
 context.momentumData={signals:[{...base,strategy:'momentum',market:'crypto',entry_min:110,entry_max:111,stop:100,target:130,roc_pct:10,efficiency:.8,extension_atr:1,version:'test'}]};
 context.csvData={reports:[],report:{id:'csv',symbol:base.symbol,timeframe:base.timeframe,strategy:'pin-bars',signals:[base],start_at:base.confirmed_at,end_at:base.confirmed_at}};
 vm.runInContext("const originalRequest=request;request=async(path,body)=>path.startsWith('/api/findings')?result:path.startsWith('/api/pin-bars')||path.startsWith('/api/bb-pin-bars')?pinData:path.startsWith('/api/momentum')?momentumData:path.startsWith('/api/csv-backtests')?csvData:originalRequest(path,body)",context);
 for(const call of ["strategyViews.live='rsi';findings('live')","strategyViews.backtest='rsi';findings('backtest')","strategyViews.live='pin-bars';pinBarFindings('pin-bars')","strategyViews.live='bb-pin-bars';pinBarFindings('bb-pin-bars')","strategyViews.live='momentum';momentumFindings('live')","strategyViews.backtest='momentum';momentumFindings('backtest')","csvBacktestFindings()"]){
  await vm.runInContext(call,context);
  const controls=descendants(get('content')).filter(e=>e.tag==='button'&&e.textContent==='Manage asset');
  assert.equal(controls.length,1,call);await controls[0].onclick();
  assert.equal(get('asset-dialog').open,true,call);assert.ok(formFor(get),call);get('asset-dialog').close();
 }
});
