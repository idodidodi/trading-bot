import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {test} from 'node:test';
const source=fs.readFileSync(new URL('./app.js',import.meta.url),'utf8');
function setup(){const ctx=vm.createContext({Intl,page:0});vm.runInContext(source.slice(source.indexOf('const signalAssetFilter='),source.indexOf('function signalAssetControls(')),ctx);return ctx;}
test('filtered pages stop fetching after enough matches without claiming a total',async()=>{
 const ctx=setup(),calls=[];
 ctx.request=async path=>{calls.push(path);return {signals:Array.from({length:50},(_,i)=>({symbol:'NEAR',direction:'bearish',timeframe:'15m',id:(calls.length-1)*50+i})),has_more:true};};
 vm.runInContext("signalAssetFilter.asset='NEAR'",ctx);
 const first=await vm.runInContext("filteredSignalPage('/api/test?view=signals','signals')",ctx);
 assert.equal(calls.length,2);assert.equal(first.signals.length,50);assert.equal(first.has_more,true);assert.equal(first.total_count,null);
 vm.runInContext('page=1',ctx);
 const next=await vm.runInContext("filteredSignalPage('/api/test?view=signals','signals')",ctx);
 assert.equal(calls.length,3);assert.equal(next.signals[0].id,50);
});
test('date filtering stops at older history and includes both repeated Israel minutes',async()=>{
 const ctx=setup(),calls=[];
 const timestamps=['2026-10-24T23:35:00Z','2026-10-24T22:35:00Z','2026-10-24T21:00:00Z'];
 ctx.request=async path=>{calls.push(path);if(calls.length>1)throw Error('Should not fetch older pages');return {signals:timestamps.map(t=>({symbol:'NEAR',confirmed_at:Date.parse(t)})),has_more:true};};
 vm.runInContext("Object.assign(signalAssetFilter,{from:'2026-10-25T01:30',to:'2026-10-25T01:40'})",ctx);
 const result=await vm.runInContext("filteredSignalPage('/api/test?view=signals','signals')",ctx);
 assert.equal(result.signals.length,2);assert.equal(result.has_more,false);assert.equal(result.total_count,2);assert.equal(calls.length,1);
});
