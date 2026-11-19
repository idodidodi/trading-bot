import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.classList={add(){},toggle(){}};this.textContent='';}
 append(...items){this.children.push(...items);} prepend(...items){this.children.unshift(...items);} replaceChildren(...items){this.children=items;}
 setAttribute(){} addEventListener(){} querySelectorAll(){return [];} close(){}
}
function setup(path){
 const nodes=new Map(),get=id=>{if(!nodes.has(id))nodes.set(id,new Element('div'));return nodes.get(id);};
 let callbacks,finish,reject,requests=0;
 const session={signedIn:()=>true,request:()=>{requests++;return new Promise((a,b)=>{finish=a;reject=b;});}};
 const context=vm.createContext({window:{DASHBOARD_CONFIG:{authProvider:'clerk'},createClerkDashboard:async(config,c)=>{callbacks=c;}},document:{getElementById:get,createElement:t=>new Element(t),createTextNode:t=>t,querySelectorAll:()=>[]},sessionStorage:{getItem:()=>null,removeItem(){}},location:{pathname:path},URLSearchParams,AbortController,setTimeout,clearTimeout,Intl,Node:Element});
 vm.runInContext(fs.readFileSync(new URL('./app.js',import.meta.url),'utf8'),context);
 return {get,ready:()=>callbacks.onReady(session),finish:()=>finish({findings:[],summary:[],scan:null}),fail:()=>reject(Object.assign(new Error('aborted'),{name:'AbortError'})),requests:()=>requests};
}
for(const path of ['/','/signals'])test(`${path} shows auth/data progress and a retryable read failure`,async()=>{
 const s=setup(path);assert.match(s.get('status').textContent,/Connecting/);s.ready();assert.match(s.get('status').textContent,/Loading/);
 s.fail();await new Promise(setImmediate);assert.match(s.get('status').textContent,/timed out/);
 const retry=s.get('status').children.find(e=>e.tag==='button'&&e.textContent==='Retry');assert.ok(retry);retry.onclick();assert.equal(s.requests(),2);
 s.finish();await new Promise(setImmediate);assert.equal(s.get('status').textContent,'');assert.ok(s.get('content').children.length);
});
