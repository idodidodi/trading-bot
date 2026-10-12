import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
let handler,valid=true,editor=true,writes=0;
const script=fs.readFileSync('cloud/functions/csv-backtests/index.ts','utf8').replace(/^import .*;\n/,'');
const context=vm.createContext({Error,JSON,Response,TextEncoder,Deno:{env:{get:()=> 'test'},serve:fn=>handler=fn},replayCSV:async input=>{if(!input.csv)throw Error('Invalid CSV');return {id:'x',signals:[],start_at:1000,end_at:2000};},fetch:async(url,options)=>{
 if(url.endsWith('/auth/v1/user'))return Response.json({}, {status:valid?200:401});
 if(url.endsWith('/rpc/dashboard_api')){assert.equal(options.headers.Authorization,'Bearer user-token');assert.equal(JSON.parse(options.body).input.action,'authorize');return Response.json({authorized:editor},{status:editor?200:403});}
 if(url.endsWith('/rpc/dashboard_save_csv_backtest')){writes++;assert.equal(options.headers.Authorization,'Bearer test');return Response.json({saved:true});}
 throw Error('Unexpected request');
}});
vm.runInContext(script,context);
const req=csv=>new Request('https://test',{method:'POST',headers:{Authorization:'Bearer user-token'},body:JSON.stringify({installation:'inst',input:{csv}})});
valid=false;assert.equal((await handler(req('data'))).status,401);assert.equal(writes,0);
valid=true;editor=false;assert.equal((await handler(req('data'))).status,403);assert.equal(writes,0);
editor=true;assert.equal((await handler(req(''))).status,400);assert.equal(writes,0);
assert.equal((await handler(req('data'))).status,200);assert.equal(writes,1);
assert.equal((await handler(new Request('https://test',{method:'OPTIONS'}))).status,200);
console.log('CSV Edge Function authentication, editor access, validation and server-only persistence checks passed.');
