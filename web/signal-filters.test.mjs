import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {test} from 'node:test';

const source=fs.readFileSync(new URL('./app.js',import.meta.url),'utf8');
function context(){
 const ctx=vm.createContext({Intl,page:0});
 vm.runInContext(source.slice(source.indexOf('const signalAssetFilter='),source.indexOf('function signalAssetControls(')),ctx);
 return ctx;
}
test('combined filters use inclusive Israel minutes, independently of browser timezone',()=>{
 const ctx=context();
 vm.runInContext(`Object.assign(signalAssetFilter,{query:' eur ',asset:'EURUSD',timeframe:'4h',direction:'bullish',from:'2026-11-27T12:00',to:'2026-11-27T12:00'});`,ctx);
 ctx.signal={symbol:'EURUSD',timeframe:'4h',direction:'bullish',confirmed_at:Date.parse('2026-11-27T10:00:59Z')};
 assert.equal(vm.runInContext('matchesSignalFilters(signal)',ctx),true);
 for(const patch of [{symbol:'BTCUSD'},{timeframe:'1h'},{direction:'bearish'},{confirmed_at:Date.parse('2026-11-27T10:01:00Z')},{confirmed_at:null}]){
  ctx.candidate={...ctx.signal,...patch};assert.equal(vm.runInContext('matchesSignalFilters(candidate)',ctx),false);
 }
 assert.equal(vm.runInContext("signalLocalMinute('2026-07-01T09:00:00Z')",ctx),'2026-07-01T12:00');
 vm.runInContext("signalAssetFilter.from='';signalAssetFilter.to='';",ctx);
 ctx.signal.confirmed_at=null;
 assert.equal(vm.runInContext('matchesSignalFilters(signal)',ctx),true);
});
test('filters apply before pagination to flat signals and nested RSI findings',async()=>{
 for(const key of ['signals','findings']){
  const ctx=context(),wrap=s=>key==='findings'?{signal:s}:s;
  const rejected=wrap({symbol:'EURUSD',timeframe:'1h',direction:'bearish'});
  const accepted=wrap({symbol:'EURUSD',timeframe:'4h',direction:'bullish'});
  ctx.request=async path=>path.endsWith('page=0')?{[key]:Array(50).fill(rejected),has_more:true}:{[key]:Array(51).fill(accepted),has_more:false};
  vm.runInContext("Object.assign(signalAssetFilter,{timeframe:'4h',direction:'bullish'});",ctx);
  ctx.key=key;
  const first=await vm.runInContext("filteredSignalPage('/api/test?view=signals',key)",ctx);
  assert.equal(first[key].length,50);assert.equal(first.total_count,51);assert.equal(first.has_more,true);
  vm.runInContext('page=1',ctx);
  const last=await vm.runInContext("filteredSignalPage('/api/test?view=signals',key)",ctx);
  assert.equal(last[key].length,1);assert.equal(last.has_more,false);
 }
});

test('initial load requests only one page; catalog and filtering reuse cached pages',async()=>{
 const ctx=context(),calls=[];
 ctx.request=async path=>{calls.push(path);return path.endsWith('page=0')?{signals:Array(50).fill({symbol:'EURUSD',timeframe:'4h'}),has_more:true}:{signals:[{symbol:'BTCUSD',timeframe:'1h'}],has_more:false};};
 const first=await vm.runInContext("filteredSignalPage('/api/test?view=signals','signals')",ctx);
 assert.equal(calls.length,1);assert.equal(first.signals.length,50);assert.equal(first.has_more,true);
 assert.deepEqual(Array.from(await first.loadAssets()),['BTCUSD','EURUSD']);assert.equal(calls.length,2);
 vm.runInContext("signalAssetFilter.asset='BTCUSD'",ctx);
 const filtered=await vm.runInContext("filteredSignalPage('/api/test?view=signals','signals')",ctx);
 assert.equal(filtered.signals.length,1);assert.equal(filtered.signals[0].symbol,'BTCUSD');assert.equal(calls.length,2);
 vm.runInContext('signalHistoryCache.clear()',ctx);
 await vm.runInContext("filteredSignalPage('/api/test?view=signals','signals')",ctx);
 assert.equal(calls.length,4);
});
test('concurrent loads share requests and obsolete responses cannot render',async()=>{
 const ctx=context();let resolve,calls=0;
 ctx.request=()=>{calls++;return new Promise(done=>resolve=done);};
 const older=vm.runInContext("filteredSignalPage('/api/test?view=signals','signals')",ctx);
 const newer=vm.runInContext("filteredSignalPage('/api/test?view=signals','signals')",ctx);
 resolve({signals:[{symbol:'EURUSD'}],has_more:false});
 await assert.rejects(older,error=>error.superseded===true);
 assert.equal((await newer).signals.length,1);assert.equal(calls,1);
});
test('failed pages can retry and expired cache retrieves new signals',async()=>{
 const ctx=context();let calls=0;
 ctx.request=async()=>{if(++calls===1)throw Error('Offline');return {signals:[{symbol:'EURUSD'}],has_more:false};};
 await assert.rejects(vm.runInContext("filteredSignalPage('/api/test?view=signals','signals')",ctx),/Offline/);
 await vm.runInContext("filteredSignalPage('/api/test?view=signals','signals')",ctx);
 vm.runInContext("signalHistoryCache.get('/api/test?view=signals').created=0",ctx);
 await vm.runInContext("filteredSignalPage('/api/test?view=signals','signals')",ctx);
 assert.equal(calls,3);
});

test('loading feedback starts immediately and only latest request may clear it',async()=>{
 const status={textContent:'',className:''},attributes={},pending=[];
 const ctx=vm.createContext({status,content:{setAttribute:(name,value)=>attributes[name]=value},renderFindings:()=>new Promise((resolve,reject)=>pending.push({resolve,reject}))});
 vm.runInContext('let signalLoadingVersion=0;'+source.slice(source.indexOf('async function findings(source){'),source.indexOf('async function renderFindings(source){')),ctx);
 const first=vm.runInContext("findings('live')",ctx);
 assert.equal(status.textContent,'Loading signals…');assert.equal(status.className,'signals-loading');assert.equal(attributes['aria-busy'],'true');
 const second=vm.runInContext("findings('live')",ctx);
 pending[0].resolve();await first;assert.equal(status.className,'signals-loading');
 pending[1].reject(Error('Offline'));await assert.rejects(second,/Offline/);
 assert.equal(status.className,'');assert.equal(attributes['aria-busy'],'false');
});
