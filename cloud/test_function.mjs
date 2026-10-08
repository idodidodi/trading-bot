// No network or credentials: exercise the Edge Function with platform APIs mocked.
import fs from 'node:fs/promises';
import vm from 'node:vm';
import assert from 'node:assert/strict';
let handler;
const settings={SUPABASE_URL:'https://example.invalid',SUPABASE_SERVICE_ROLE_KEY:'test-service',SYNC_TOKEN:'test-machine',INSTALLATION_ID:'test-installation'};
const ids=new Set(),published=[];
const context={Deno:{env:{get:k=>settings[k]},serve:fn=>handler=fn},Request,Response,URL,URLSearchParams,JSON,Number,Date,Set,fetch:async(url,options={})=>{
 const parsed=new URL(url);
 if(parsed.pathname.endsWith('/rpc/dashboard_ingest')){const body=JSON.parse(options.body);for(const record of body.records)if(record.kind==='backtest')ids.add(record.key);return Response.json({ack:['event'],conflicts:[],config:null});}
 if(parsed.pathname.endsWith('/dashboard_records')){const values=parsed.searchParams.get('key')?.match(/\((.*)\)/)?.[1]?.split(',')||[];return Response.json(values.filter(value=>ids.has(value)).map(key=>({key})));}
 if(parsed.pathname.endsWith('/dashboard_backtest_jobs')&&options.method==='POST'){published.push(JSON.parse(options.body)[0]);return new Response(null,{status:201});}
 return Response.json([]);
}};
vm.runInNewContext(await fs.readFile(new URL('./functions/sync/index.ts',import.meta.url),'utf8'),context);
assert.equal((await handler(new Request('https://example.invalid',{method:'POST'}))).status,401);
const request=body=>new Request('https://example.invalid',{method:'POST',headers:{Authorization:'Bearer test-machine'},body:JSON.stringify(body)});
assert.equal((await handler(request({records:[],cursor:-1}))).status,400);
assert.equal((await handler(request({records:Array(101).fill({}),cursor:0}))).status,400);
const response=await handler(request({records:[],cursor:0,config_base:0}));assert.equal(response.status,200);
const result=await response.json();assert.deepEqual(result.ack,['event']);assert.deepEqual(result.finding_states,[]);
const findingId='b'.repeat(64),reportKey='a'.repeat(64);
const reportResponse=await handler(request({records:[{kind:'backtest',key:findingId,payload:{}},{kind:'backtest_run',key:reportKey,payload:{year:2026,generated_at:Date.now()/1000,results:[{asset:'BTCUSD',timeframe:'4h'}],summary:[{signals:1}],finding_ids:[findingId]}}],cursor:0,config_base:0}));
assert.equal(reportResponse.status,200);assert.equal((await reportResponse.json()).backtest_publication,'published');assert.equal(published.length,1);assert.equal(published[0].status,'completed');assert.deepEqual(published[0].finding_ids,[findingId]);
assert.equal((await handler(request({records:[],cursor:0,state_cursor:-1}))).status,400);
console.log('Edge function checks passed: machine auth, batch validation, acknowledgement, and publishing a fully synced report.');
