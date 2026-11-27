import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {test} from 'node:test';
const source=fs.readFileSync(new URL('./app.js',import.meta.url),'utf8');
class Element{
 constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];this.attributes={};this.value='';this.isConnected=true;}
 append(...children){this.children.push(...children);}
 replaceChildren(...children){this.children=children;}
 setAttribute(key,value){this.attributes[key]=value;}
 focus(){this.focused=true;}
 contains(element){return this===element||this.children.some(child=>child.contains?.(element));}
 querySelector(tag){return descend(this).find(item=>item!==this&&item.tag===tag);}
}
const descend=element=>[element,...element.children.flatMap(descend)];
function setup(){
 const node=(tag,text)=>new Element(tag,text),button=(text,onclick)=>Object.assign(node('button',text),{onclick});
 const ctx=vm.createContext({node,button,signalAssetFilter:{asset:'',from:'',to:''},strategyViews:{live:'rsi'},page:3,findings:async()=>{},message:()=>{}});
 vm.runInContext(source.slice(source.indexOf('let activeSignalPopup='),source.indexOf('function signalAssetControls(')),ctx);
 return ctx;
}
test('choice popups share selection, search, Escape, and single-open behavior',()=>{
 const ctx=setup();
 const frame=vm.runInContext("signalChoiceControl('Timeframe',[['','All timeframes'],['4h','4 hours'],['1h','1 hour']],'')",ctx);
 frame.open();assert.equal(frame.popup.hidden,false);assert.equal(frame.trigger.attributes['aria-expanded'],'true');
 const search=descend(frame.popup).find(item=>item.tag==='input');search.value='4';search.oninput();
 const four=descend(frame.popup).find(item=>item.tag==='button'&&item.textContent==='4 hours');four.onclick();
 assert.equal(frame.selection.value,'4h');assert.equal(frame.trigger.textContent,'4 hours');assert.equal(frame.popup.hidden,true);
 frame.open();
 const direction=vm.runInContext("signalChoiceControl('Direction',[['','All directions'],['bullish','Bullish']],'')",ctx);
 direction.open();assert.equal(frame.popup.hidden,true);
 direction.popup.onkeydown({key:'Escape',preventDefault(){}});assert.equal(direction.popup.hidden,true);assert.equal(direction.trigger.focused,true);
});
test('date popup preserves native bounds, validates range, and summarizes or clears it',()=>{
 const ctx=setup(),date=vm.runInContext('signalDateRangeControl()',ctx);
 assert.equal(date.trigger.textContent,'Any date & time');assert.equal(date.fields.from.type,'datetime-local');
 date.open();date.fields.from.value='2026-11-27T13:00';date.fields.to.value='2026-11-26T13:00';date.fields.from.oninput();
 const done=descend(date.popup).find(item=>item.tag==='button'&&item.textContent==='Done');done.onclick();
 assert.equal(date.popup.hidden,false);assert.match(descend(date.popup).find(item=>item.attributes.role==='alert').textContent,/start must be before/);
 date.fields.to.value='2026-11-27T14:00';done.onclick();assert.equal(date.popup.hidden,true);assert.match(date.trigger.textContent,/2026-11-27 13:00 → 2026-11-27 14:00/);
 descend(date.popup).find(item=>item.tag==='button'&&item.textContent==='Clear range').onclick();assert.equal(date.fields.from.value,'');assert.equal(date.fields.to.value,'');assert.equal(date.trigger.textContent,'Any date & time');
});
test('live strategy uses the same popup and changes strategy immediately',()=>{
 const ctx=setup();vm.runInContext(source.slice(source.indexOf('function strategySelector('),source.indexOf('const signalAssetFilter=')),ctx);
 const strategy=vm.runInContext("strategySelector('live')",ctx);
 const trigger=descend(strategy).find(item=>item.attributes['aria-label']==='Strategy view');trigger.onclick();
 descend(strategy).find(item=>item.tag==='button'&&item.textContent==='Pin-Bars').onclick();
 assert.equal(ctx.strategyViews.live,'pin-bars');assert.equal(ctx.page,0);
});
test('closing asset popup cancels catalog continuation without surfacing an error',async()=>{
 const ctx=setup();let current,reject;
 ctx.loadAssets=isCurrent=>{current=isCurrent;return new Promise((resolve,fail)=>reject=fail);};
 const asset=vm.runInContext("searchableSignalAsset(['EURUSD'],loadAssets)",ctx);
 const trigger=descend(asset.element).find(item=>item.attributes['aria-label']==='Filter by asset');trigger.onclick();assert.equal(current(),true);
 const popup=descend(asset.element).find(item=>item.attributes.role==='dialog');popup.onkeydown({key:'Escape',preventDefault(){}});assert.equal(current(),false);
 reject(Object.assign(Error('Superseded'),{superseded:true}));await new Promise(resolve=>setImmediate(resolve));
 assert.equal(descend(asset.element).find(item=>item.attributes.role==='status').textContent,'');
});
