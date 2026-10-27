import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {test} from 'node:test';

class Element {
 constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.listeners={};this.classList={add(){},toggle(){}};this.textContent='';}
 append(...items){this.children.push(...items);}
 replaceChildren(...items){this.children=items;}
 setAttribute(){}
 addEventListener(name,fn){this.listeners[name]=fn;}
 showModal(){this.open=true;}
 close(){this.open=false;this.listeners.close?.();}
}
const text=e=>[e.textContent,...e.children.map(c=>c instanceof Element?text(c):String(c))].join(' ');
function setup(value,testResponse={message:'Connection test passed.'},hosted=false){
 const nodes=new Map(),get=id=>{if(!nodes.has(id))nodes.set(id,new Element('div'));return nodes.get(id);};
 const requests=[];
 const context=vm.createContext({window:{DASHBOARD_CONFIG:hosted?{supabaseUrl:'https://cloud.test'}:{}},document:{getElementById:get,createElement:t=>new Element(t)},sessionStorage:{getItem:()=>null},location:{pathname:'/logs'},fetch:async (url,options)=>{requests.push({url,options});return {ok:!testResponse.error||url!=='/api/connection-test',json:async()=>url==='/api/versions'?await value:url==='/api/connection-test'?await testResponse:{rows:[]}};},URLSearchParams,AbortController,setTimeout,clearTimeout,Intl,Node:Element});
 vm.runInContext(fs.readFileSync(new URL('./app.js',import.meta.url),'utf8'),context);
 return {context,get,requests};
}
const descendants=e=>[e,...e.children.filter(c=>c instanceof Element).flatMap(descendants)];
test('gear connection buttons send one explicit action and show success or failure in the dialog',async()=>{
 for(const error of [false,true]){
  const {get,requests}=setup({environment:'prod',local:{version:'1.0.6'}},error?{error:'Cloud unavailable'}:{message:'One demo synced'});
  await get('settings').onclick();
  const controls=descendants(get('version-content')).filter(e=>e.tag==='button'&&['Test Telegram','Sync demo signal to cloud'].includes(e.textContent));
  assert.equal(controls.length,2);
  assert.ok(!requests.some(r=>r.url==='/api/connection-test'));
  for(const [index,action] of ['telegram','cloud-demo'].entries()){
   await controls[index].onclick();
   assert.equal(controls[index].disabled,false);
   assert.equal(JSON.parse(requests.filter(r=>r.url==='/api/connection-test').at(-1).options.body).action,action);
   assert.match(text(get('version-content')),error?/Cloud unavailable/:/One demo synced/);
  }
 }
});
test('hosted dashboard does not show local credential test buttons',()=>{
 const {context,get}=setup({}, {},true);
 vm.runInContext("connectionTestControls(document.getElementById('test-panel'))",context);
 assert.equal(get('test-panel').children.length,0);
});
test('gear shows environment, distinct UI/scanner versions, repository matches and release',async()=>{
 const {get}=setup({environment:'dev',local:{version:'1.0.5',ui:'local-ui',scanner:'scanner-a'},online_ui:{ui:'online-ui'},github:{ui:'local-ui',scanner:'scanner-b',commit:'abcdef1234567890'},release:{version:'v1.0.6'},updates:{supported:false}});
 await get('settings').onclick();
 assert.equal(get('settings-dialog').open,true);
 const output=text(get('version-content'));
 for(const expected of ['Environment: dev','Local UI','local-ui','Online UI','online-ui','Local scanner','scanner-a','Matches GitHub','Differs from GitHub','1.0.5','v1.0.6'])assert.ok(output.includes(expected),expected);
 get('close-settings').onclick();assert.equal(get('settings-dialog').open,false);
});
test('offline version check preserves installed version and reports unavailable comparisons',async()=>{
 const {get}=setup({environment:'prod',local:{version:'1.0.5',ui:'a',scanner:'b'},github:{error:'Unavailable'},online_ui:{error:'Unavailable'},release:{error:'Unavailable'},updates:{supported:true,message:'Idle'}});
 await get('settings').onclick();
 const output=text(get('version-content'));
 assert.ok(output.includes('1.0.5'));assert.ok(output.includes('Comparison unavailable'));
 assert.ok(output.includes('Online UI: Unavailable'));assert.ok(output.includes('Check for app update'));
 assert.ok(!output.includes('Matches GitHub'));
});
test('development settings show explicitly enabled Telegram and cloud sync',async()=>{
 const {get}=setup({environment:'dev',telegram_enabled:true,cloud_sync_enabled:true,local:{version:'test'}});
 await get('settings').onclick();
 const output=text(get('version-content'));
 assert.ok(output.includes('Telegram delivery: enabled.'));
 assert.ok(output.includes('Cloud sync: enabled'));
 assert.ok(output.includes('paper orders disabled'));
 assert.ok(!output.includes('deliveries disabled'));
});
test('closing the dialog discards a pending version response',async()=>{
 let resolve;const pending=new Promise(r=>{resolve=r;});const {get}=setup(pending);
 const loading=get('settings').onclick();get('close-settings').onclick();
 resolve({environment:'dev',local:{version:'late'}});await loading;
 assert.equal(text(get('version-content')),' Checking versions…');
});

test('Telegram routing renders thirteen destinations and saves numeric strings with revision',async()=>{
 const {context,get}=setup({});
 const calls=[];
 context.request=async(path,body)=>{calls.push({path,body});return body?{revision:5,routes:body.routes,editable:true}:{revision:4,routes:{general:'-100123','forex.rsi':'-100456'},editable:true};};
 await context.telegramRoutingControls(get('routes'),()=>true);
 const controls=descendants(get('routes'));
 const inputs=controls.filter(e=>e.tag==='input');
 assert.equal(inputs.length,13);
 assert.equal(inputs[0].value,'-100123');
 inputs[1].value=' -100789 ';
 const save=controls.find(e=>e.textContent==='Save Telegram groups');
 await save.onclick();
 assert.equal(calls.length,2);
 assert.equal(calls[1].path,'/api/telegram-routing');
 assert.equal(calls[1].body.revision,4);
 assert.equal(calls[1].body.routes['forex.rsi'],'-100789');
 assert.ok(text(get('routes')).includes('Connected scanners will refresh'));
 inputs[1].value='@invalid';await save.onclick();assert.equal(calls.length,2);
});

test('cached Telegram routes are read only and stale dialog responses are discarded',async()=>{
 const {context,get}=setup({});
 context.request=async()=>({revision:4,routes:{general:'-100123'},editable:false});
 await context.telegramRoutingControls(get('routes'),()=>true);
 const controls=descendants(get('routes'));
 assert.equal(controls.filter(e=>e.tag==='input'&&e.disabled).length,13);
 assert.ok(!controls.some(e=>e.textContent==='Save Telegram groups'));
 await context.telegramRoutingControls(get('stale'),()=>false);
 assert.equal(descendants(get('stale')).filter(e=>e.tag==='input').length,0);
});
