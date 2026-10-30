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
const fn=source.slice(source.indexOf('async function personalTelegramControls('),source.indexOf('async function telegramRoutingControls('));
function setup(online=true){
 const calls=[],panel=new Element('section');
 const ctx=vm.createContext({online,node:(tag,text)=>new Element(tag,text),document:{createTextNode:t=>t},button:(text,action)=>Object.assign(new Element('button',text),{onclick:action}),request:async(path,body)=>{calls.push({path,body});return body?{revision:2,enabled:body.enabled,registered:true}:{revision:1,enabled:{'crypto.rsi':false},registered:true};}});
 vm.runInContext(fn,ctx);
 return {ctx,calls,panel};
}
test('personal checkboxes default on, preserve explicit off, and save without a user override',async()=>{
 const {ctx,calls,panel}=setup();await ctx.personalTelegramControls(panel,()=>true);
 const checkboxes=descendants(panel).filter(e=>e.tag==='input');
 assert.equal(checkboxes.length,13);
 assert.equal(checkboxes.filter(e=>e.checked).length,12);
 assert.equal(checkboxes.find(e=>e.attributes['aria-label']==='Receive Crypto · RSI alerts').checked,false);
 checkboxes[0].checked=false;
 const save=descendants(panel).find(e=>e.tag==='button');await save.onclick();
 assert.equal(calls[1].path,'/api/telegram-alerts');
 assert.equal(calls[1].body.enabled.general,false);
 assert.equal(calls[1].body.enabled['crypto.rsi'],false);
 assert.equal(calls[1].body.revision,1);
 assert.equal('user_id' in calls[1].body,false);
 assert.equal(save.disabled,false);
});
test('local dashboard directs users to signed-in online settings',async()=>{
 const {ctx,calls,panel}=setup(false);await ctx.personalTelegramControls(panel,()=>true);
 assert.equal(calls.length,0);assert.equal(descendants(panel).filter(e=>e.tag==='input').length,0);
});
