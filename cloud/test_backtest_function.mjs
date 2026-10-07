import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import {detect,validateCandles,followupWindows} from './functions/backtests/engine.mjs';
const script=fs.readFileSync('cloud/functions/backtests/index.ts','utf8').replace(/^import .*;\n/,'');
let handler,background,writes=[],owner=true,validToken=true,invalidHistory=false;
const rules={rsi_period:3,bb_period:20,bb_multiplier:2,pivot_left:2,pivot_right:1,min_spacing:5,max_spacing:60};
const context=vm.createContext({Error,detect,validateCandles,followupWindows,URLSearchParams,Response,crypto,TextEncoder,Deno:{env:{get:k=>k==='SUPABASE_URL'?'https://test.supabase.co':'server-key'},serve:fn=>handler=fn},EdgeRuntime:{waitUntil:p=>background=p},fetch:async(url,options={})=>{
 if(url.endsWith('/auth/v1/user'))return Response.json({}, {status:validToken?200:401});
 if(url.endsWith('/rpc/dashboard_api')){assert.equal(options.headers.Authorization,'Bearer user-token');return Response.json(owner?{id:'job',status:'running'}:{error:'Not authorized'},{status:owner?200:403});}
 if(url.includes('kind=eq.backtest_run'))return Response.json([{payload:{rules}}]);
 if(url.includes('kind=eq.candle'))return Response.json(invalidHistory?[{payload:{start:1000,end:2000,open:2,high:1,low:3,close:2}}]:[]);
 if(url.endsWith('/rpc/dashboard_finish_backtest')||url.endsWith('/rpc/dashboard_fail_backtest')){writes.push({url,body:JSON.parse(options.body)});return Response.json({completed:true});}
 throw Error('Unexpected URL '+url);
}});
vm.runInContext(script,context);
const request=()=>new Request('https://test/functions/v1/backtests',{method:'POST',headers:{Authorization:'Bearer user-token','Content-Type':'application/json'},body:JSON.stringify({installation:'inst',input:{assets:['TEST'],timeframes:['daily'],strategy:'confirmed'}})});
validToken=false;assert.equal((await handler(request())).status,401);assert.equal(writes.length,0);
validToken=true;owner=false;assert.equal((await handler(request())).status,400);assert.equal(writes.length,0);
owner=true;assert.equal((await handler(request())).status,202);await background;assert.equal(writes[0].body.summary[0].status,'unavailable');assert.equal(writes[0].body.findings.length,0);
writes=[];invalidHistory=true;assert.equal((await handler(request())).status,202);await background;assert.ok(writes[0].url.endsWith('/rpc/dashboard_fail_backtest'));assert.match(writes[0].body.reason,/Invalid/);
assert.equal((await handler(new Request('https://test',{method:'OPTIONS'}))).status,200);
console.log('Cloud function checks passed: user authentication, owner rejection, asynchronous completion, missing data and failed OHLC validation.');
