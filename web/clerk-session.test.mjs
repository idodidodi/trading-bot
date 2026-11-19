import {test} from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
const source=fs.readFileSync(new URL('./clerk-session.js',import.meta.url),'utf8');
function setup(signedIn=true){
 const nodes=new Map(),scripts=[],requests=[],mounts=[];
 const document={getElementById:id=>{if(!nodes.has(id))nodes.set(id,{replaceChildren(){},hidden:false});return nodes.get(id);},createElement:()=>({setAttribute(){}}),head:{appendChild:script=>{scripts.push(script);queueMicrotask(()=>script.onload());}}};
 const session={id:'sess_one',getToken:async()=>'signed-token'};
 const clerk={session:signedIn?session:null,load:async()=>{},addListener:fn=>{clerk.listener=fn;},mountUserButton:()=>mounts.push('account'),mountSignIn:()=>mounts.push('signin')};
 const window={document,Clerk:clerk};let response=async()=>({ok:true,json:async()=>({rows:['private']})}),changes=0;
 const context=vm.createContext({window,URL,atob,AbortController,setTimeout,clearTimeout,fetch:async(...args)=>{requests.push(args);return response();}});
 vm.runInContext(source,context);
 const config={clerkPublishableKey:'pk_test_'+btoa('example.clerk.accounts.dev$'),apiOrigin:'https://api.example.com',installationId:'fixed-id'};
 return {clerk,scripts,requests,mounts,config,create:()=>window.createClerkDashboard(config,{onReady(){},onIdentityChange(){changes++;}}),respond:fn=>{response=fn;},changes:()=>changes};
}
test('loads official bundles and mounts account control, sends token only to configured API',async()=>{
 const s=setup(),auth=await s.create();assert.deepEqual(s.mounts,['account']);assert.equal(s.scripts.length,2);
 assert.match(s.scripts[1].src,/^https:\/\/example\.clerk\.accounts\.dev\/npm\/@clerk\/clerk-js@6/);
 assert.equal((await auth.request('/api/logs')).rows[0],'private');
 const [url,options]=s.requests[0];assert.equal(url,'https://api.example.com/api/dashboard');
 assert.equal(options.headers.Authorization,'Bearer signed-token');assert.equal(options.redirect,'error');assert.equal(options.credentials,'omit');
 assert.deepEqual(JSON.parse(options.body),{installation:'fixed-id',operation:'/api/logs',input:{}});
});
test('signed out renders sign-in and cannot request data',async()=>{
 const s=setup(false),auth=await s.create();assert.deepEqual(s.mounts,['signin']);
 await assert.rejects(auth.request('/api/logs'),/sign in/);assert.equal(s.requests.length,0);
});
test('account switch discards an in-flight response',async()=>{
 const s=setup(),auth=await s.create();let finish;
 s.respond(()=>new Promise(resolve=>{finish=resolve;}));const pending=auth.request('/api/logs');
 await new Promise(resolve=>setImmediate(resolve));
 s.clerk.session={id:'sess_two',getToken:async()=>'other-token'};s.clerk.listener({session:s.clerk.session});
 finish({ok:true,json:async()=>({rows:['old-user-data']})});
 await assert.rejects(pending,/session changed/);assert.equal(s.changes(),1);
});
test('sign-out during token refresh prevents network request',async()=>{
 const s=setup();let finish;s.clerk.session.getToken=()=>new Promise(resolve=>{finish=resolve;});const auth=await s.create();
 const pending=auth.request('/api/logs');s.clerk.session=null;s.clerk.listener({session:null});finish('old-token');
 await assert.rejects(pending,/session changed/);assert.equal(s.requests.length,0);
});
test('invalid configuration and arbitrary paths fail closed',async()=>{
 const s=setup();s.config.apiOrigin='http://remote.example.com';await assert.rejects(s.create(),/origin/);assert.equal(s.scripts.length,0);
 s.config.apiOrigin='https://api.example.com';const auth=await s.create();await assert.rejects(auth.request('https://attacker.example'),/path/);assert.equal(s.requests.length,0);
});
test('API permission failures are not presented as data',async()=>{
 const s=setup(),auth=await s.create();s.respond(async()=>({ok:false,status:403,json:async()=>({error:'Not authorized'})}));
 await assert.rejects(auth.request('/api/logs'),/Not authorized/);
});
