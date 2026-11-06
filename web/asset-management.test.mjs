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
function setup(settings,saveError=null,hosted=false){
 const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,new Element('div'));return elements.get(id);};
 const requests=[];
 const context=vm.createContext({window:hosted?{DASHBOARD_CONFIG:{supabaseUrl:'https://test.invalid'}}:{},document:{getElementById:get,createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag)},sessionStorage:{getItem:()=>null},location:{pathname:'/logs'},fetch:async(url,options)=>{
  const body=options.body?JSON.parse(options.body):null;requests.push({url,body});
  if(url==='/api/assets'){
   if(body?.action==='bulk'){settings={...settings,revision:settings.revision+1,config:{...settings.config,assets:[...settings.config.assets,{id:body.tickers,provider:'csv',enabled:false,timeframes:['daily']}]}};}
   if(body?.action==='save'&&saveError)return {ok:false,json:async()=>({error:saveError})};
   if(body?.action==='save')settings={...settings,revision:settings.revision+1,config:{...settings.config,assets:settings.config.assets.map(a=>a.id===body.asset.id?{...a,...body.asset}:a)},results:[{ticker:body.asset.id,status:'saved'}]};
   if(body?.action==='remove')settings={...settings,revision:settings.revision+1,config:{...settings.config,assets:settings.config.assets.filter(a=>a.id!==body.ticker)},results:[{ticker:body.ticker,status:'removed'}]};
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

test('Kraken lower timeframe selection survives saving without changing CSV backtest intervals',async()=>{
 const lower={...asset,provider:'kraken',exchange:'Kraken',timeframes:['30m','15m','5m'],timeframe_providers:{}};
 const {context,get,requests}=setup({...settings,config:{...settings.config,assets:[lower]}});
 await vm.runInContext("openAssetManager({symbol:'kraken:Kraken:BTC/USD',timeframe:'5m'})",context);
 const form=formFor(get),selected=descendants(form).filter(e=>e.tag==='input'&&['5m','15m','30m'].includes(e.value));
 assert.deepEqual(selected.filter(e=>e.checked).map(e=>e.value),['30m','15m','5m']);
 await submit(form);
 assert.deepEqual(requests.find(r=>r.body?.action==='save').body.asset.timeframes,['30m','15m','5m']);
 assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(frames)',context)),['monthly','weekly','daily','4h','1h']);
});

test('new intraday choices remain opt-in for legacy assets',async()=>{
 const legacy={...asset};delete legacy.timeframes;
 const {context,get}=setup({...settings,config:{assets:[legacy]}});
 await vm.runInContext("openAssetManager({symbol:'BTCUSD'})",context);
 const selected=descendants(formFor(get)).filter(e=>e.tag==='input'&&vm.runInContext('assetFrames',context).includes(e.value));
 assert.deepEqual(selected.filter(e=>e.checked).map(e=>e.value),['monthly','weekly','daily','4h']);
});

test('strategy selections start from saved settings and save added and removed strategies with the timeframes',async()=>{
 const {context,get,requests}=setup(settings);
 await vm.runInContext("openAssetManager({symbol:'BTCUSD'})",context);
 const form=formFor(get),strategies=descendants(form).filter(e=>e.tag==='input'&&['rsi','pin-bars','bb-pin-bars','rsi-pb'].includes(e.value));
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
 const form=formFor(get),strategies=descendants(form).filter(e=>e.tag==='input'&&['rsi','pin-bars','bb-pin-bars','rsi-pb'].includes(e.value));
 assert.deepEqual(strategies.filter(i=>i.checked).map(i=>i.value),['rsi','pin-bars','rsi-pb']);
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

test('subscriber can request a catalogue forex pair without changing an asset or owner identity',async()=>{
 const {context,get}=setup(settings,null,true);
 context.colmexValue={revision:3,symbols:['EURUSD'],feed:{status:'collecting',updated_at:Date.now()/1000,catalogue:[{symbol:'EURUSD',export_supported:true,forex:true},{symbol:'GBPUSD',export_supported:true,forex:true},{symbol:'SPX500#',export_supported:true}],terminal:{assets:[]}},coverage:[]};
 context.savedRequests=[];
 vm.runInContext("request=async(path,body)=>{if(body)savedRequests.push({path,body});return colmexValue}",context);
 await vm.runInContext("loadColmexRequests(document.getElementById('content'))",context);
 const all=descendants(get('content')),form=all.find(e=>e.tag==='form'),inputs=all.filter(e=>e.tag==='input');
 assert.equal(inputs.length,2);inputs[1].checked=true;
 await submit(form);
 const saved=context.savedRequests[0];assert.equal(saved.path,'/api/colmex');
 assert.deepEqual(JSON.parse(JSON.stringify(saved.body)),{action:'set',revision:3,symbols:['EURUSD','GBPUSD']});
 assert.match(text(get('content')),/does not mean candles are ready/);
});

test('stale Colmex catalogue allows existing pair removal but hides new pair choices',async()=>{
 const {context,get}=setup(settings,null,true);
 context.colmexValue={revision:1,symbols:['EURUSD'],feed:{status:'collecting',updated_at:0,catalogue:[{symbol:'GBPUSD',export_supported:true,forex:true}]}};
 vm.runInContext('request=async()=>colmexValue',context);
 await vm.runInContext("loadColmexRequests(document.getElementById('content'))",context);
 assert.equal(descendants(get('content')).filter(e=>e.tag==='input').length,1);
 assert.match(text(get('content')),/unavailable or stale/);
});

const emptyLibrary={revision:1,applied_revision:1,config:{assets:[],poll_seconds:3600},coverage:[]};
const chooseCategory=(get,label)=>descendants(get('asset-content')).find(e=>e.attrs['aria-label']===label).onclick();
const findButton=(root,title)=>descendants(root).find(e=>e.tag==='button'&&e.textContent===title);

test('rebuilt library shows cards with a focused edit action, not embedded settings',async()=>{
 const {context,get,requests}=setup(settings);await vm.runInContext('loadAssets()',context);
 const elements=descendants(get('content'));assert.equal(elements.filter(e=>e.tag==='article').length,1);
 assert.equal(elements.filter(e=>e.tag==='fieldset'||e.tag==='select').length,0);
 assert.doesNotMatch(text(get('content')),/Primary provider|Provider symbol|I verified|Configuration 17/);
 await findButton(get('content'),'Edit asset').onclick();assert.equal(get('asset-dialog').open,true);
 const form=formFor(get);assert.match(text(form),/ASSET SETTINGS/);await submit(form);
 const saved=requests.find(r=>r.body?.action==='save').body.asset;
 assert.deepEqual(saved.timeframe_providers,asset.timeframe_providers);assert.equal(saved.provider,asset.provider);assert.equal(saved.path,asset.path);assert.equal(saved.tradingview_symbol,asset.tradingview_symbol);
});

test('search and category filters combine; clear filters restores all cards without writes',async()=>{
 const current={...settings,config:{assets:[asset,{id:'MU',symbol:'MU',name:'Micron',market:'stock',provider:'alpaca',enabled:false,timeframes:['daily']}]}};
 const {context,get,requests}=setup(current);await vm.runInContext('loadAssets()',context);
 const search=descendants(get('content')).find(e=>e.attrs['aria-label']==='Search assets');search.value='Micron';search.oninput();
 assert.equal(descendants(get('content')).filter(e=>e.tag==='article').length,1);assert.match(text(get('content')),/MU/);
 findButton(get('content'),'Crypto').onclick();assert.match(text(get('content')),/No matching assets/);
 findButton(get('content'),'Clear filters').onclick();assert.equal(descendants(get('content')).filter(e=>e.tag==='article').length,2);assert.ok(!requests.some(r=>r.body));
});

test('empty library offers setup without creating anything until final confirmation',async()=>{
 const {context,get,requests}=setup(emptyLibrary);await vm.runInContext('loadAssets()',context);assert.match(text(get('content')),/Start with your first asset/);
 await findButton(get('content'),'Add an asset').onclick();assert.match(text(formFor(get)),/STEP 1 OF 2/);
 findButton(get('asset-content'),'Cancel').onclick();assert.ok(!requests.some(r=>r.body));
});

test('two-step add asks for category and configures one default provider only on final save',async()=>{
 const {context,get,requests}=setup(emptyLibrary);await vm.runInContext("openAssetSetup('MU')",context);
 await submit(formFor(get));assert.match(text(formFor(get)),/What category is this asset/);assert.ok(!requests.some(r=>r.body));
 chooseCategory(get,'Stock / ETF');await submit(formFor(get));assert.match(text(formFor(get)),/STEP 2 OF 2/);assert.match(text(formFor(get)),/Alpaca/);assert.equal(descendants(formFor(get)).filter(e=>e.tag==='select').length,0);assert.ok(!requests.some(r=>r.body));
 await submit(formFor(get));const saved=requests.find(r=>r.body?.action==='save').body;
 assert.equal(saved.revision,2);assert.equal(saved.asset.symbol,'MU');assert.equal(saved.asset.provider,'alpaca');assert.deepEqual(saved.asset.timeframes,['monthly','weekly','daily','4h']);assert.equal(get('asset-dialog').open,false);
});

test('back preserves scanning choices; inferred crypto preserves quote currency and excludes monthly',async()=>{
 const {context,get,requests}=setup(emptyLibrary);await vm.runInContext("openAssetSetup('BTCUSDT')",context);await submit(formFor(get));
 let inputs=descendants(formFor(get)).filter(e=>e.tag==='input');assert.equal(inputs.find(e=>e.value==='monthly').disabled,true);
 for(const i of inputs.filter(e=>e.value&&['weekly','daily','4h'].includes(e.value)))i.checked=i.value==='daily';
 findButton(get('asset-content'),'Back').onclick();await submit(formFor(get));await submit(formFor(get));
 const saved=requests.find(r=>r.body?.action==='save').body.asset;assert.equal(saved.symbol,'BTC/USDT');assert.equal(saved.provider,'kraken');assert.deepEqual(saved.timeframes,['daily']);
});

test('missing quote and duplicate assets cannot advance; exact Other mapping is explicit',async()=>{
 const {context,get,requests}=setup(settings);await vm.runInContext("openAssetSetup('BTC/USD')",context);await submit(formFor(get));assert.match(text(formFor(get)),/Already in your assets/);assert.ok(!requests.some(r=>r.body));
 const other=setup(emptyLibrary);await vm.runInContext("openAssetSetup('SOL')",other.context);chooseCategory(other.get,'Crypto');await submit(formFor(other.get));assert.match(text(formFor(other.get)),/quote currency/);
 chooseCategory(other.get,'Other');await submit(formFor(other.get));assert.match(text(formFor(other.get)),/exact supported instrument/);
 const input=descendants(formFor(other.get)).find(e=>e.attrs['aria-label']==='Asset symbol');input.value='CL=F';input.oninput();descendants(formFor(other.get)).find(e=>e.attrs.role==='option'&&text(e).includes('CL=F')).onclick();await submit(formFor(other.get));assert.match(text(formFor(other.get)),/CL=F/);await submit(formFor(other.get));assert.equal(other.requests.find(r=>r.body?.action==='save').body.asset.market,'futures');
});

test('failed final save leaves paused draft and retries without duplicate creation',async()=>{
 const {context,get,requests}=setup(emptyLibrary,'Changed elsewhere');await vm.runInContext("openAssetSetup('EURUSD')",context);await submit(formFor(get));await submit(formFor(get));
 assert.match(text(formFor(get)),/paused draft remains/);assert.equal(get('asset-dialog').open,true);assert.equal(findButton(get('asset-content'),'Back'),undefined);
 await submit(formFor(get));assert.equal(requests.filter(r=>r.body?.action==='bulk').length,1);assert.equal(requests.filter(r=>r.body?.action==='save').length,2);
});

test('remove requires explicit confirmation; cancelling retains the asset',async()=>{
 const {context,get,requests}=setup(settings);await vm.runInContext("openAssetSetup('BTCUSD','BTCUSD')",context);
 findButton(get('asset-content'),'Remove asset').onclick();assert.match(text(formFor(get)),/Stored signal history will remain/);assert.ok(!requests.some(r=>r.body));
 findButton(get('asset-content'),'Keep asset').onclick();assert.ok(!requests.some(r=>r.body));
 findButton(get('asset-content'),'Remove asset').onclick();await findButton(get('asset-content'),'Yes, remove').onclick();const removed=requests.find(r=>r.body?.action==='remove').body;assert.equal(removed.ticker,'BTCUSD');assert.equal(removed.revision,17);assert.equal(get('asset-dialog').open,false);
});

test('existing CSV draft starts at identification and retains paused state and strategies',async()=>{
 const draft={id:'MU',provider:'csv',enabled:false,timeframes:['daily'],scan_strategies:['bb-pin-bars']};const {context,get,requests}=setup({...emptyLibrary,config:{assets:[draft]}});
 await vm.runInContext("openAssetSetup('MU','MU')",context);assert.match(text(formFor(get)),/Find an asset to add/);chooseCategory(get,'Stock / ETF');await submit(formFor(get));await submit(formFor(get));
 assert.ok(!requests.some(r=>r.body?.action==='bulk'));const saved=requests.find(r=>r.body?.action==='save').body.asset;assert.equal(saved.enabled,false);assert.deepEqual(saved.scan_strategies,['bb-pin-bars']);assert.deepEqual(saved.timeframes,['daily']);
});

test('empty scanning selection blocks save and stale coverage cannot imply data readiness',async()=>{
 const {context,get,requests}=setup(settings);await vm.runInContext("openAssetSetup('BTCUSD','BTCUSD')",context);
 for(const input of descendants(formFor(get)).filter(e=>e.tag==='input'&&e.value))input.checked=false;
 await submit(formFor(get));assert.match(text(formFor(get)),/at least one timeframe and one strategy/);assert.ok(!requests.some(r=>r.body));
 context.sample={...asset,timeframe_providers:{},timeframes:['daily']};context.snapshot={revision:2,applied_revision:1,coverage:[{asset:asset.id,symbol:asset.symbol,provider:asset.provider,timeframe:'daily',status:'ready',last_closed_at:123}]};
 assert.equal(vm.runInContext('assetHealth(sample,snapshot).label',context),'Checking data');context.snapshot.applied_revision=2;assert.equal(vm.runInContext('assetHealth(sample,snapshot).label',context),'Data received');
});

test('cached dropdown searches symbols and names within the selected category without requests',async()=>{
 const {context,get,requests}=setup(emptyLibrary);await vm.runInContext("openAssetSetup()",context);
 chooseCategory(get,'Stock / ETF');const input=descendants(formFor(get)).find(e=>e.attrs.role==='combobox');const before=requests.length;
 input.value='Micron';input.oninput();let options=descendants(formFor(get)).filter(e=>e.attrs.role==='option');assert.equal(options.length,1);assert.match(text(options[0]),/MU.*Micron/);
 assert.equal(descendants(formFor(get)).find(e=>e.attrs['aria-label']==='Stock / ETF').attrs['aria-pressed'],'true');assert.equal(requests.length,before);
 options[0].onclick();assert.equal(input.value,'MU');assert.equal(input.attrs['aria-expanded'],'false');await submit(formFor(get));assert.match(text(formFor(get)),/Alpaca/);
});

test('changing category filters the cached dropdown and never silently resets while typing',async()=>{
 const {context,get}=setup(emptyLibrary);await vm.runInContext('openAssetSetup()',context);chooseCategory(get,'Crypto');const input=descendants(formFor(get)).find(e=>e.attrs.role==='combobox');input.value='USD';input.oninput();
 let options=descendants(formFor(get)).filter(e=>e.attrs.role==='option');assert.ok(options.length);assert.ok(options.every(e=>text(e).includes('Crypto')));
 chooseCategory(get,'Forex');options=descendants(formFor(get)).filter(e=>e.attrs.role==='option');assert.ok(options.length);assert.ok(options.every(e=>text(e).includes('Forex')));
 input.value='Bitcoin';input.oninput();assert.match(text(formFor(get)),/No cached matches/);assert.equal(descendants(formFor(get)).find(e=>e.attrs['aria-label']==='Forex').attrs['aria-pressed'],'true');
});

test('keyboard selection skips existing assets and Escape closes the dropdown without leaving setup',async()=>{
 const {context,get,requests}=setup(settings);await vm.runInContext("openAssetSetup('BTC')",context);chooseCategory(get,'Crypto');const input=descendants(formFor(get)).find(e=>e.attrs.role==='combobox');input.onfocus();
 const options=descendants(formFor(get)).filter(e=>e.attrs.role==='option');const owned=options.find(e=>text(e).includes('BTC/USD '));assert.equal(owned.attrs['aria-disabled'],'true');owned.onclick();assert.match(text(formFor(get)),/management section/);assert.ok(!requests.some(r=>r.body));
 const key=key=>({key,preventDefault(){},stopPropagation(){}});input.onkeydown(key('ArrowDown'));assert.ok(input.attrs['aria-activedescendant']);input.onkeydown(key('Enter'));assert.equal(input.value,'BTC/USDT');assert.equal(input.attrs['aria-expanded'],'false');
 input.onfocus();input.onkeydown(key('Escape'));assert.equal(input.attrs['aria-expanded'],'false');assert.equal(get('asset-dialog').open,true);
});

test('add-new and management sections are distinct while saved cards stay unchanged',async()=>{
 const {context,get}=setup(settings);await vm.runInContext('loadAssets()',context);const all=descendants(get('content'));
 assert.ok(all.some(e=>e.tag==='h3'&&e.textContent==='Add a new asset'));assert.ok(all.some(e=>e.tag==='h3'&&e.textContent==='Manage existing assets'));
 assert.equal(all.filter(e=>e.tag==='article').length,1);assert.equal(all.filter(e=>e.attrs['aria-label']==='Search assets').length,1);assert.equal(all.filter(e=>e.attrs.role==='combobox').length,0);
});
