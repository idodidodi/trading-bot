import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {test} from 'node:test';

function harness(respond=()=>({rows:[]})){
 class Element{
  constructor(tag){this.tag=tag;this.children=[];this.textContent='';}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  setAttribute(){} addEventListener(){}
  click(){downloads.push({filename:this.download,url:this.href});}
  remove(){this.removed=true;}
 }
 const downloads=[],blobs=[],requests=[],elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,new Element('div'));return elements.get(id);};
 const context=vm.createContext({window:{},document:{body:new Element('body'),getElementById:get,createElement:tag=>new Element(tag)},sessionStorage:{getItem:()=>null},location:{pathname:'/logs'},fetch:async url=>{requests.push(url);return {ok:true,json:async()=>respond(url)};},Blob,URL:{createObjectURL:blob=>{blobs.push(blob);return 'blob:export';},revokeObjectURL(){}},URLSearchParams,AbortController,setTimeout,clearTimeout,Intl,Node:Element});
 vm.runInContext(fs.readFileSync(new URL('./app.js',import.meta.url),'utf8'),context);
 return {context,downloads,blobs,requests,get};
}

test('CSV escapes quotes, commas, multiline feedback and formulas; preserves numeric outcomes',()=>{
 const {context}=harness();
 const csv=vm.runInContext(`csvText([{symbol:'A,B',feedback:{comment:'Say "yes"\\nnext'},followup:{drawdown_pct:-2.5}},{symbol:'=HYPERLINK("bad")',feedback:{comment:'  +SUM(1,2)'}}])`,context);
 assert.ok(csv.startsWith('\uFEFF'));
 assert.match(csv, /"feedback.comment","followup.drawdown_pct"/);
 assert.ok(csv.includes('"A,B"'));
 assert.ok(csv.includes('"Say ""yes""\nnext"'));
 assert.ok(csv.includes('"-2.5"'));
 assert.ok(csv.includes('"\'=HYPERLINK(""bad"")"'));
 assert.ok(csv.includes('"\'  +SUM(1,2)"'));
 assert.equal(vm.runInContext('csvText([])',context),'\uFEFF"symbol","timeframe","direction","confirmed_at"\r\n');
});

test('export downloads all pages of the selected report and includes nested forward outcomes',async()=>{
 const {context,downloads,blobs,requests}=harness(url=>url.startsWith('/api/csv-backtests')?{report:{signals:[{symbol:url.endsWith('page=0')?'FIRST':'LAST',forward:{5:{directional_close_pct:3.25}}}],has_more:url.endsWith('page=0')}}:{rows:[]});
 const b=vm.runInContext(`exportCSVButton('backtest-TVC:USOIL',()=>allResultPages('/api/csv-backtests?id=saved-report','report'))`,context);
 await b.onclick();
 assert.equal(b.disabled,false);
 assert.deepEqual(downloads,[{filename:'backtest-TVC_USOIL.csv',url:'blob:export'}]);
 assert.deepEqual(requests.filter(r=>r.startsWith('/api/csv-backtests')),['/api/csv-backtests?id=saved-report&page=0','/api/csv-backtests?id=saved-report&page=1']);
 const csv=await blobs[0].text();
 assert.match(csv,/FIRST/);assert.match(csv,/LAST/);assert.match(csv,/forward.5.directional_close_pct/);
});

test('historical findings export preserves sort order and stops on failed requests without a partial download',async()=>{
 const {context,downloads,requests,get}=harness(url=>{if(url.startsWith('/api/findings')){if(url.endsWith('page=1'))return {error:'Network failure'};return {findings:[{id:'first'}],has_more:true};}return {rows:[]};});
 const b=vm.runInContext(`exportCSVButton('backtest',()=>allResultPages('/api/findings?source=backtest&sort=rating&order=desc','findings'))`,context);
 await b.onclick();
 assert.equal(b.disabled,false);assert.equal(downloads.length,0);
 assert.equal(get('status').textContent,'Network failure');
 assert.ok(requests.includes('/api/findings?source=backtest&sort=rating&order=desc&page=1'));
});
