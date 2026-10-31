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
 const {ctx,calls,panel}=setup();await ctx.chasingControls(panel,()=>true);
 const pct=descendants(panel).find(e=>e.tag==='input'&&e.type==='number');
 assert.equal(pct.value,.5);pct.value='1.25';
 await descendants(panel).find(e=>e.tag==='button').onclick();
 const save=calls.find(c=>c.body);assert.equal(save.body.stop_pct,1.25);
 assert.equal(save.body.revision,1);assert.equal('user_id' in save.body,false);
});
test('invalid percentage is rejected before saving',async()=>{
 const {ctx,calls,panel}=setup();await ctx.chasingControls(panel,()=>true);
 const pct=descendants(panel).find(e=>e.tag==='input'&&e.type==='number');pct.value='0';
 await descendants(panel).find(e=>e.tag==='button').onclick();
 assert.equal(calls.filter(c=>c.body).length,0);
});
