import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {test} from 'node:test';
class Element{constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];this.attributes={};}append(...items){this.children.push(...items);}setAttribute(k,v){this.attributes[k]=v;}}
const source=fs.readFileSync(new URL('app.js',import.meta.url),'utf8');
const fn=source.slice(source.indexOf('async function nearGroupControls('),source.indexOf('async function chasingControls('));
const all=e=>[e,...e.children.flatMap(all)];
test('NEAR grid preserves choices and saves an empty selection without destination overrides',async()=>{
 const panel=new Element('section'),calls=[];
 const ctx=vm.createContext({online:true,node:(tag,text)=>new Element(tag,text),button:(text,onclick)=>Object.assign(new Element('button',text),{onclick}),request:async(path,body)=>{calls.push({path,body});return body?{...body,revision:4}:{revision:3,editable:true,timeframes:['5m','15m','30m','1h','4h','daily','weekly'],subscriptions:{rsi:['5m','1h'],'trend-pin-bars':['15m']}};}});
 vm.runInContext(fn,ctx);await ctx.nearGroupControls(panel,()=>true);
 const checkboxes=all(panel).filter(e=>e.tag==='input');assert.equal(checkboxes.length,14);assert.equal(checkboxes.filter(e=>e.checked).length,3);
 for(const input of checkboxes)input.checked=false;
 await all(panel).find(e=>e.tag==='button').onclick();
 assert.equal(calls[1].path,'/api/near-alerts');assert.equal(calls[1].body.revision,3);
 assert.equal(JSON.stringify(calls[1].body.subscriptions),'{"rsi":[],"trend-pin-bars":[]}');
 assert.equal('chat' in calls[1].body,false);
});
