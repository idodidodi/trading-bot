import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {test} from 'node:test';
const source=fs.readFileSync(new URL('./app.js',import.meta.url),'utf8');
function setup(){const ctx=vm.createContext({Intl,page:0});vm.runInContext(source.slice(source.indexOf('const signalAssetFilter='),source.indexOf('function signalAssetControls(')),ctx);return ctx;}
test('sparse and empty results never trigger a history download',async()=>{
 const ctx=setup(),calls=[];
 ctx.request=async(path,body)=>{calls.push(body);return {signals:[],has_more:false,next_cursor:null,total_count:null};};
 vm.runInContext("Object.assign(signalAssetFilter,{asset:'NEAR',from:'2020-01-01T00:00',to:'2020-01-01T01:00'})",ctx);
 const result=await vm.runInContext("filteredSignalPage('/api/pin-bars?view=signals','signals')",ctx);
 assert.equal(calls.length,1);assert.equal(result.signals.length,0);assert.equal(calls[0].from,'2020-01-01T00:00');
});
test('cache expiry on a later page preserves the cursor without refetching earlier pages',async()=>{
 const ctx=setup(),calls=[];
 ctx.request=async(path,body)=>{calls.push(body);return {signals:[],has_more:true,next_cursor:{time:100-calls.length,id:String(calls.length)}};};
 await vm.runInContext("filteredSignalPage('/api/pin-bars?view=signals','signals')",ctx);
 vm.runInContext('page=1',ctx);
 await vm.runInContext("filteredSignalPage('/api/pin-bars?view=signals','signals')",ctx);
 vm.runInContext('[...signalHistoryCache.values()][0].pages[1].created=0',ctx);
 await vm.runInContext("filteredSignalPage('/api/pin-bars?view=signals','signals')",ctx);
 assert.equal(calls.length,3);assert.equal(calls[2].cursor.id,'1');
});
