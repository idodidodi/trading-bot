// No network or credentials: exercise the Edge Function with platform APIs mocked.
import fs from 'node:fs/promises';
import vm from 'node:vm';
import assert from 'node:assert/strict';
let handler;
const settings={SUPABASE_URL:'https://example.invalid',SUPABASE_SERVICE_ROLE_KEY:'test-service',SYNC_TOKEN:'test-machine',INSTALLATION_ID:'test-installation'};
const context={Deno:{env:{get:k=>settings[k]},serve:fn=>handler=fn},Request,Response,URLSearchParams,JSON,Number,fetch:async(url)=>Response.json(url.includes('/rpc/')?{ack:['event'],conflicts:[],config:null}:[])};
vm.runInNewContext(await fs.readFile(new URL('./functions/sync/index.ts',import.meta.url),'utf8'),context);
assert.equal((await handler(new Request('https://example.invalid',{method:'POST'}))).status,401);
const request=body=>new Request('https://example.invalid',{method:'POST',headers:{Authorization:'Bearer test-machine'},body:JSON.stringify(body)});
assert.equal((await handler(request({records:[],cursor:-1}))).status,400);
assert.equal((await handler(request({records:Array(101).fill({}),cursor:0}))).status,400);
const response=await handler(request({records:[],cursor:0,config_base:0}));assert.equal(response.status,200);
const result=await response.json();assert.deepEqual(result.ack,['event']);assert.deepEqual(result.finding_states,[]);
assert.equal((await handler(request({records:[],cursor:0,state_cursor:-1}))).status,400);
console.log('Edge function checks passed: machine auth, batch validation, successful acknowledgement.');
