import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {test} from 'node:test';
class Element{
 constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];this.attributes={};}
 append(...items){this.children.push(...items);}
 setAttribute(key,value){this.attributes[key]=value;}
}
const descendants=e=>[e,...e.children.filter(x=>x instanceof Element).flatMap(descendants)];
const source=fs.readFileSync(new URL('app.js',import.meta.url),'utf8');
const fn=source.slice(source.indexOf('async function chasingControls('),source.indexOf('function settingsLoading('));
function setup(){
 const calls=[],panel=new Element('section');
 const ctx=vm.createContext({online:true,node:(tag,text)=>new Element(tag,text),document:{createTextNode:t=>t},button:(text,action)=>Object.assign(new Element('button',text),{onclick:action}),request:async(path,body)=>{
  calls.push({path,body});if(path==='/api/chasing-registration')throw Error('Owner required');
  return {revision:body?2:1,stop_pct:.5,chasing:false,live_positions:false,groups:{},sessions:[]};
 }});vm.runInContext(fn,ctx);return {ctx,calls,panel};
}
test('personal numeric stop and message preferences save together without a supplied user ID',async()=>{
 const {ctx,calls,panel}=setup();await ctx.archivedChasingControls(panel,()=>true);
 const pct=descendants(panel).find(e=>e.tag==='input'&&e.type==='number');
 assert.equal(pct.value,.5);pct.value='1.25';
 await descendants(panel).find(e=>e.tag==='button').onclick();
 const save=calls.find(c=>c.body);assert.equal(save.body.stop_pct,1.25);
 assert.equal(save.body.revision,1);assert.equal('user_id' in save.body,false);
});
test('invalid percentage is rejected before saving',async()=>{
 const {ctx,calls,panel}=setup();await ctx.archivedChasingControls(panel,()=>true);
 const pct=descendants(panel).find(e=>e.tag==='input'&&e.type==='number');pct.value='0';
 await descendants(panel).find(e=>e.tag==='button').onclick();
 assert.equal(calls.filter(c=>c.body).length,0);
});
test('automatic crypto Chase saves its own destination and revision',async()=>{
 const {ctx,calls,panel}=setup();
 ctx.request=async(path,body)=>{calls.push({path,body});return body?{revision:3,...body}:{revision:2,enabled:false,chat:'-10003'};};
 await ctx.archivedCryptoAutoChaseControls(panel,()=>true);
 const controls=descendants(panel),enabled=controls.find(e=>e.tag==='input'&&e.type==='checkbox'),chat=controls.find(e=>e.tag==='input'&&e.type==='text');
 enabled.checked=true;chat.value='-10005';
 await controls.find(e=>e.tag==='button').onclick();
 const saved=calls.find(c=>c.body);assert.equal(saved.path,'/api/crypto-auto-chase-settings');
 assert.equal(saved.body.revision,2);assert.equal(saved.body.enabled,true);assert.equal(saved.body.chat,'-10005');assert.equal('user_id' in saved.body,false);
});
test('automatic crypto Chase validates enabled destination and discards stale responses',async()=>{
 const {ctx,calls,panel}=setup();ctx.request=async()=>({revision:0,enabled:false,chat:''});
 await ctx.archivedCryptoAutoChaseControls(panel,()=>true);
 const controls=descendants(panel);controls.find(e=>e.tag==='input'&&e.type==='checkbox').checked=true;
 await controls.find(e=>e.tag==='button').onclick();assert.equal(calls.length,0);
 const stale=new Element('section');await ctx.archivedCryptoAutoChaseControls(stale,()=>false);
 assert.equal(descendants(stale).filter(e=>e.tag==='input').length,0);
});

test('suspended settings expose no activation or chat controls',async()=>{
 const {ctx,calls,panel}=setup();
 await ctx.chasingControls(panel,()=>true);await ctx.cryptoAutoChaseControls(panel,()=>true);
 assert.equal(calls.length,0);assert.equal(descendants(panel).filter(e=>e.tag==='input'||e.tag==='button').length,0);
 assert.match(descendants(panel).map(e=>e.textContent).join(' '),/suspended/);
});
