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
test('one request per filtered page, with exact filters and server cursors',async()=>{
 for(const key of ['signals','findings']){
  const ctx=context(),calls=[];
  ctx.request=async(path,body)=>{calls.push({path,body});return {[key]:[{symbol:'EURUSD',signal:{symbol:'EURUSD'}}],has_more:!body.cursor,next_cursor:body.cursor?null:{time:123,id:'last'}};};
  vm.runInContext("Object.assign(signalAssetFilter,{asset:'EURUSD',timeframe:'4h',direction:'bullish',from:'2026-11-27T12:00',to:'2026-11-27T13:00'});",ctx);
  ctx.key=key;
  await vm.runInContext("filteredSignalPage('/api/pin-bars?view=signals',key)",ctx);
  assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/signal-query');
  assert.equal(calls[0].body.timeframe,'4h');assert.equal(calls[0].body.asset,'EURUSD');
  assert.equal(calls[0].body.from,'2026-11-27T12:00');assert.equal(calls[0].body.cursor,null);
  vm.runInContext('page=1',ctx);
  await vm.runInContext("filteredSignalPage('/api/pin-bars?view=signals',key)",ctx);
  assert.equal(calls.length,2);assert.equal(calls[1].body.cursor.id,'last');
  vm.runInContext('page=0',ctx);
  await vm.runInContext("filteredSignalPage('/api/pin-bars?view=signals',key)",ctx);
  assert.equal(calls.length,2);
 }
});
test('asset catalog uses one compact request, independent of result pagination',async()=>{
 const ctx=context(),calls=[];
 ctx.request=async(path,body)=>{calls.push(body);return body.catalog?{assets:['BTCUSD','EURUSD']}:{signals:[{symbol:'EURUSD'}],has_more:true,next_cursor:{time:1,id:'a'}};};
 const first=await vm.runInContext("filteredSignalPage('/api/pin-bars?view=signals','signals')",ctx);
 assert.equal(calls.length,1);
 assert.deepEqual(Array.from(await first.loadAssets()),['BTCUSD','EURUSD']);assert.equal(calls.length,2);
 assert.equal(calls[1].catalog,true);await first.loadAssets();assert.equal(calls.length,2);
 vm.runInContext("signalAssetFilter.asset='BTCUSD'",ctx);
 await vm.runInContext("filteredSignalPage('/api/pin-bars?view=signals','signals')",ctx);
 assert.equal(calls.length,3);assert.equal(calls[2].asset,'BTCUSD');
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
 vm.runInContext("[...signalHistoryCache.values()][0].pages[0].created=0",ctx);
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

test('asset catalogs share in-flight requests and cache across filter changes but isolate strategy and archives',async()=>{
 const ctx=context();let finish,catalogCalls=0;
 ctx.request=async(path,body)=>body.catalog?(catalogCalls++,await new Promise(resolve=>finish=resolve)):{signals:[],has_more:false};
 const first=await vm.runInContext("filteredSignalPage('/api/pin-bars?view=signals','signals')",ctx);
 const one=first.loadAssets(),two=first.loadAssets();assert.equal(catalogCalls,1);
 finish({assets:['A','B']});await Promise.all([one,two]);
 vm.runInContext("signalAssetFilter.timeframe='4h'",ctx);
 const filtered=await vm.runInContext("filteredSignalPage('/api/pin-bars?view=signals','signals')",ctx);
 assert.deepEqual(Array.from(await filtered.loadAssets()),['A','B']);assert.equal(catalogCalls,1);
 const other=await vm.runInContext("filteredSignalPage('/api/bb-pin-bars?view=signals','signals')",ctx);
 const three=other.loadAssets();assert.equal(catalogCalls,2);finish({assets:['C']});await three;
 vm.runInContext("[...signalCatalogCache.values()][0].created=0",ctx);
 const expired=first.loadAssets();assert.equal(catalogCalls,3);finish({assets:['A','B','D']});await expired;
});
