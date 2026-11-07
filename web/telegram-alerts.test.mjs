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
 const ctx=vm.createContext({Intl:{DateTimeFormat:Object.assign(function(...args){return args.length?new Intl.DateTimeFormat(...args):{resolvedOptions:()=>({timeZone:"Asia/Jerusalem"})};},{})},online,node:(tag,text)=>new Element(tag,text),document:{createTextNode:t=>t},button:(text,action)=>Object.assign(new Element('button',text),{onclick:action}),request:async(path,body)=>{calls.push({path,body});return body?{revision:2,enabled:body.enabled,registered:true}:{revision:1,enabled:{'crypto.rsi':false},registered:true};}});
 vm.runInContext(fn,ctx);
 return {ctx,calls,panel};
}
test('personal checkboxes default on, preserve explicit off, and save without a user override',async()=>{
 const {ctx,calls,panel}=setup();await ctx.personalTelegramControls(panel,()=>true);
 const checkboxes=descendants(panel).filter(e=>e.tag==='input'&&e.attributes['aria-label']?.startsWith('Receive '));
 assert.equal(checkboxes.length,14);
 assert.equal(checkboxes.filter(e=>e.checked).length,13);
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

test('silent choices, overnight hours and validation are saved personally',async()=>{
 const {ctx,calls,panel}=setup();await ctx.personalTelegramControls(panel,()=>true);
 const elements=descendants(panel), find=label=>elements.find(e=>e.attributes['aria-label']===label);
 const silent=find('Deliver General silently');silent.checked=true;
 const receive=find('Receive Crypto · RSI alerts'),muted=find('Deliver Crypto · RSI silently');
 assert.equal(muted.disabled,true);receive.checked=true;receive.onchange();assert.equal(muted.disabled,false);
 const enabled=elements.find(e=>e.tag==='input'&&e.type==='checkbox'&&!e.attributes['aria-label']);enabled.checked=true;
 find('Quiet hours start').value='22:00';find('Quiet hours end').value='07:00';assert.equal(find('Quiet hours local timezone').value,'Asia/Jerusalem');assert.equal(find('Quiet hours local timezone').readOnly,true);
 const save=elements.find(e=>e.tag==='button');await save.onclick();
 assert.equal(calls[1].body.silent.general,true);assert.equal(calls[1].body.quiet_hours.enabled,true);
 assert.equal(calls[1].body.quiet_hours.timezone,'Asia/Jerusalem');
 find('Quiet hours end').value='22:00';await save.onclick();assert.equal(calls.length,2);
 find('Quiet hours end').value='07:00';const buttons=elements.filter(e=>e.tag==='button');assert.equal(buttons.length,2);assert.equal(buttons[0].onclick,buttons[1].onclick);await buttons[1].onclick();assert.equal(calls.length,3);assert.equal(calls[2].body.revision,2);
 assert.equal(elements.find(e=>e.tag==='fieldset').children.includes(buttons[0]),true);
 assert.ok(elements.filter(e=>e.attributes.role==='status').every(e=>e.textContent.startsWith('Saved.')));
});

test('all new notification controls default unchecked without changing subscriptions',async()=>{
 const {ctx,panel}=setup();await ctx.personalTelegramControls(panel,()=>true);
 const boxes=descendants(panel).filter(e=>e.tag==='input'&&e.type==='checkbox');
 const newBoxes=boxes.filter(e=>!e.attributes['aria-label']?.startsWith('Receive '));
 assert.equal(newBoxes.length,15);assert.ok(newBoxes.every(e=>!e.checked));
 assert.equal(boxes.filter(e=>e.attributes['aria-label']?.startsWith('Receive ')&&e.checked).length,13);
});
