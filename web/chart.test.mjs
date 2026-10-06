import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {test} from 'node:test';
const {indicators,draw}=await import('data:text/javascript;base64,'+fs.readFileSync(new URL('./chart.js',import.meta.url)).toString('base64'));
const rules={rsi_period:3,bb_period:3,bb_multiplier:2};
const candles=[10,12,11,14,13].map((close,i)=>({start:(i+1)*1000,end:(i+2)*1000,open:close,high:close+1,low:close-1,close}));

test('Wilder RSI seed/recursion and population Bollinger Bands preserve warm-up gaps',()=>{
 const values=indicators(candles,rules);
 assert.deepEqual(values.rsi.map(p=>p.time),[4,5]);
 assert.ok(Math.abs(values.rsi[0].value-100*5/6)<1e-10);
 assert.ok(Math.abs(values.rsi[1].value-100*10/15)<1e-10);
 assert.equal(values.middle[0].value,11);
 assert.ok(Math.abs(values.upper[0].value-(11+2*Math.sqrt(2/3)))<1e-10);
 assert.ok(Math.abs(values.lower[0].value-(11-2*Math.sqrt(2/3)))<1e-10);
 assert.deepEqual(indicators(candles.slice(0,2),rules),{rsi:[],lower:[],middle:[],upper:[]});
 for(const [change,value] of [[0,50],[1,100],[-1,0]]){
  assert.equal(indicators(candles.map((c,i)=>({...c,close:10+i*change})),rules).rsi.at(-1).value,value);
 }
});

test('draw overlays bands and puts RSI on a separate pane with a 0–100 scale',async()=>{
 const series=[],heights=[],markers=[];let range;
 const chart={addSeries(type,options={},pane=0){const s={type,options,pane,setData(data){this.data=data;},createPriceLine(line){(this.lines??=[]).push(line);}};series.push(s);return s;},panes:()=>[0,1].map(i=>({setHeight:h=>heights[i]=h})),timeScale:()=>({setVisibleRange:r=>range=r,fitContent(){}})};
 globalThis.window={LightweightCharts:true};
 globalThis.LightweightCharts={createChart:()=>chart,CandlestickSeries:'candle',LineSeries:'line',LineStyle:{Dashed:2},createSeriesMarkers:(s,m)=>markers.push(...m)};
 await draw({},candles,{rules,pivot1:2000,pivot2:3000,confirmed_at:5000},'confirmation');
 assert.equal(series.length,5);
 assert.deepEqual(series.slice(1,4).map(s=>s.pane),[0,0,0]);
 assert.equal(series[4].pane,1);
 assert.deepEqual(series[4].options.autoscaleInfoProvider().priceRange,{minValue:0,maxValue:100});
 assert.deepEqual(series[4].lines.map(l=>l.price),[30,70]);
 assert.equal(markers.find(m=>m.text==='Confirmed').time,4);
 assert.equal(range.from,1);
 assert.ok(heights[0]>heights[1]);
});

test('closing a direct candle page returns to its findings; an in-page dialog stays put',async()=>{
 for(const [path,source,destination] of [['/candle','live','/'],['/candle','backtest','/backtest'],['/dashboard','live',undefined]]){
  const elements=new Map();
  const element=()=>({textContent:'',append(){},replaceChildren(){},addEventListener(event,fn){this[event]=fn;},showModal(){this.open=true;},close(){this.open=false;this['close-event']?.();}});
  const get=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id);};
  get('candle-dialog').addEventListener=(event,fn)=>{get('candle-dialog')['close-event']=fn;};
  let navigated;
  const data={signal:{symbol:'BTCUSD',timeframe:'4h',rules},candles:[],note:'Source candles'};
  const context=vm.createContext({window:{},document:{getElementById:get,createElement:element},sessionStorage:{getItem:()=>null},location:{pathname:path,search:`?source=${source}&finding=test`,assign:url=>navigated=url},fetch:async()=>({ok:true,json:async()=>path==='/candle'?data:{findings:[],has_more:false}}),URLSearchParams,AbortController,setTimeout,clearTimeout,Intl,Node:class{}});
  vm.runInContext(fs.readFileSync(new URL('./app.js',import.meta.url),'utf8'),context);
  await new Promise(resolve=>setImmediate(resolve));
  if(path==='/candle'){
   assert.equal(get('candle-title').textContent,'BTCUSD · 4 hours candles');
   assert.match(get('candle-legend').textContent,/Bollinger Bands \(3, 2σ\).*RSI \(3, Wilder\)/);
  }
  get('close-chart').onclick();
  assert.equal(navigated,destination);
 }
});
