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
function setup(value){
 const nodes=new Map(),get=id=>{if(!nodes.has(id))nodes.set(id,new Element('div'));return nodes.get(id);};
 const context=vm.createContext({window:{},document:{getElementById:get,createElement:t=>new Element(t)},sessionStorage:{getItem:()=>null},location:{pathname:'/logs'},fetch:async url=>({ok:true,json:async()=>url==='/api/versions'?await value:{rows:[]}}),URLSearchParams,AbortController,setTimeout,clearTimeout,Intl,Node:Element});
 vm.runInContext(fs.readFileSync(new URL('./app.js',import.meta.url),'utf8'),context);
 return {context,get};
}
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
test('closing the dialog discards a pending version response',async()=>{
 let resolve;const pending=new Promise(r=>{resolve=r;});const {get}=setup(pending);
 const loading=get('settings').onclick();get('close-settings').onclick();
 resolve({environment:'dev',local:{version:'late'}});await loading;
 assert.equal(text(get('version-content')),' Checking versions…');
});
