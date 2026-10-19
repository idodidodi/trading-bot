import fs from 'node:fs/promises';
import vm from 'node:vm';
import assert from 'node:assert/strict';
let handler, calls=[], fail=false;
const settings={SUPABASE_URL:'https://example.invalid',SUPABASE_SERVICE_ROLE_KEY:'service-secret',SYNC_TOKEN:'machine-secret',INSTALLATION_ID:'server-installation'};
vm.runInNewContext(await fs.readFile(new URL('./functions/telegram-delivery/index.ts',import.meta.url),'utf8'),{
 Deno:{env:{get:k=>settings[k]},serve:fn=>handler=fn},Response,Number,Object,JSON,AbortSignal,
 fetch:async(url,options)=>{calls.push({url,options});if(fail)throw Error('service-secret');return Response.json({status:'acquired'});}
});
const base={action:'acquire',destination_hash:'a'.repeat(64),event_hash:'b'.repeat(64),body_hash:'c'.repeat(64),owner:'11111111-1111-4111-8111-111111111111',sender_id:'22222222-2222-4222-8222-222222222222',device:'home-pc',environment:'dev'};
const request=(body=base,token='machine-secret',method='POST')=>new Request('https://example.invalid',{method,headers:{Authorization:'Bearer '+token},...(method==='POST'?{body:typeof body==='string'?body:JSON.stringify(body)}:{})});
assert.equal((await handler(request(base,'wrong'))).status,401);
assert.equal((await handler(request(base,'machine-secret','GET'))).status,405);
for(const body of [null,{},'{', 'x'.repeat(4097), {...base,action:'delete'}, {...base,owner:'bad'}, {...base,event_hash:'bad'}, {...base,environment:'other'}, {...base,device:'x'.repeat(81)}, {...base,message_id:-1}, {...base,silent:'false'}, {...base,silent:null}]){
 assert.ok([400,413].includes((await handler(request(body))).status));
}
assert.equal(calls.length,0);
for(const action of ['acquire','renew','complete','release'])assert.equal((await handler(request({...base,action,installation:'attacker',token:'private'}))).status,200);
const sent=JSON.parse(calls[0].options.body);
assert.equal(sent.installation,'server-installation');assert.equal(sent.input.installation,undefined);assert.equal(sent.input.token,undefined);
assert.equal(calls[0].options.headers.Authorization,'Bearer service-secret');
for(const silent of [false,true]){
 assert.equal((await handler(request({...base,action:'renew',silent}))).status,200);
 assert.equal(JSON.parse(calls.at(-1).options.body).input.silent,silent);
}
fail=true;const unavailable=await handler(request());assert.equal(unavailable.status,503);assert.equal(await unavailable.text(),'Coordination unavailable');
settings.INSTALLATION_ID='';assert.equal((await handler(request())).status,503);
console.log('Telegram endpoint checks passed: auth, validation, server scope, field whitelist, and sanitized failures.');
