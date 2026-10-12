import {detect,validateCandles} from '../backtests/engine.mjs';
import {defaults} from './rules.mjs';
export function detectPinBars(bars,rules,source,symbol,timeframe){
 let fast=bars[0]?.close,slow=fast;const signals=[];
 for(let i=0;i<bars.length;i++){
  const b=bars[i];fast+=2/(rules.fast+1)*(b.close-fast);slow+=2/(rules.slow+1)*(b.close-slow);
  if(i<rules.slow)continue;
  const body=Math.abs(b.open-b.close),lower=Math.min(b.open,b.close)-b.low,upper=b.high-Math.max(b.open,b.close);
  const bullish=fast>slow&&b.close>slow&&lower>body*rules.ratio&&lower>upper&&(b.low<=fast||b.low<=slow);
  const bearish=fast<slow&&b.close<slow&&upper>body*rules.ratio&&upper>lower&&(b.high>=fast||b.high>=slow);
  if(bullish||bearish)signals.push({strategy:'pin-bars',version:rules.version,source,symbol,timeframe,direction:bullish?'bullish':'bearish',candle_at:b.start,confirmed_at:b.end,open:b.open,high:b.high,low:b.low,close:b.close,fast_ema:fast,slow_ema:slow,body,lower_wick:lower,upper_wick:upper,rules});
 }return signals;
}
function csvRows(text){
 const rows=[];let row=[],field='',quoted=false;
 for(let i=0;i<text.length;i++){
  const c=text[i];
  if(c==='"'){if(quoted&&text[i+1]==='"'){field+='"';i++;}else quoted=!quoted;}
  else if(!quoted&&(c===','||c==='\n'||c==='\r')){row.push(field);field='';if(c!==','){if(c==='\r'&&text[i+1]==='\n')i++;if(row.some(v=>v!==''))rows.push(row);row=[];if(rows.length>20001)throw Error('CSV exceeds 20,000 candles');}}
  else field+=c;
 }
 if(quoted)throw Error('Unclosed CSV quote');
 if(field||row.length){row.push(field);rows.push(row);}return rows;
}
function parseTime(value){
 if(/^\d+(?:\.\d+)?$/.test(value)){const n=Number(value);return Math.trunc(n>=1e12?n:n*1000);}
 if(!/(Z|[+-]\d\d:\d\d)$/.test(value))throw Error('Candle timestamps must include a timezone');
 const n=Date.parse(value);if(!Number.isFinite(n))throw Error('Invalid timestamp');return n;
}
function closeAt(start,frame){
 if(frame==='monthly'){const d=new Date(start),day=d.getUTCDate();d.setUTCMonth(d.getUTCMonth()+1,1);const last=new Date(Date.UTC(d.getUTCFullYear(),d.getUTCMonth()+1,0)).getUTCDate();d.setUTCDate(Math.min(day,last));return d.getTime();}
 return start+({'1h':3600000,'4h':14400000,daily:86400000,weekly:604800000})[frame];
}
export function parseCSV(text,frame,now){
 if(typeof text!=='string'||!text.length||new TextEncoder().encode(text).length>2000000)throw Error('CSV must be at most 2 MB');
 if(!['1h','4h','daily','weekly','monthly'].includes(frame))throw Error('Select the CSV candle timeframe');
 const rows=csvRows(text.replace(/^\uFEFF/,'')),header=rows.shift()||[],columns=Object.fromEntries(header.map((h,i)=>[h,i]));
 if(!['open','high','low','close'].every(k=>k in columns))throw Error('CSV requires open, high, low and close columns');
 const key='timestamp' in columns?'timestamp':'time' in columns?'time':null;
 if(!key)throw Error('CSV requires time or timestamp');
 if(!rows.length||rows.length>20000)throw Error('CSV must contain 1–20,000 candles');
 const explicit='closed_at' in columns;
 const bars=rows.map(row=>{const start=parseTime((row[columns[key]]||'').trim()),c={start,end:explicit&&row[columns.closed_at]?parseTime(row[columns.closed_at].trim()):closeAt(start,frame)};for(const k of ['open','high','low','close']){const v=row[columns[k]];if(v==null||!v.trim())throw Error('Malformed CSV OHLC');c[k]=Number(v);}return c;}).sort((a,b)=>a.start-b.start);
 validateCandles(bars);const closed=bars.filter(c=>c.end<=now);
 if(!closed.length)throw Error('CSV has no completed candles');
 return {bars:closed,excluded:bars.length-closed.length,alignment:explicit?'explicit closed_at':'nominal timeframe from each start; session closes are not verified'};
}
async function hash(value){return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value)))).map(v=>v.toString(16).padStart(2,'0')).join('');}
export async function replayCSV(body,now=Date.now(),parameters=defaults){
 const strategy=body.strategy||'pin-bars',symbol=body.symbol,frame=body.timeframe;
 if(!['pin-bars','confirmed','warmup'].includes(strategy))throw Error('Select Pin-Bars or RSI divergence');
 if(typeof symbol!=='string'||!/^[A-Za-z0-9_:./!\-]{1,100}$/.test(symbol))throw Error('Enter an exact symbol or instrument label');
 const {bars,excluded,alignment}=parseCSV(body.csv,frame,now),rules=parameters[strategy==='pin-bars'?'pin-bars':'rsi'];
 const minimum=strategy==='pin-bars'?rules.slow+1:Math.max(rules.bb_period+rules.pivot_left+rules.pivot_right,rules.max_spacing+rules.pivot_left+rules.pivot_right+1,rules.rsi_period+1);
 if(bars.length<minimum)throw Error(`Need ${minimum} completed candles; CSV has ${bars.length}`);
 const source='uploaded-csv:'+(await hash(body.csv)).slice(0,16),signals=strategy==='pin-bars'?detectPinBars(bars,rules,source,symbol,frame):detect(bars,rules,symbol,frame,strategy==='warmup');
 for(const s of signals){let lo=0,hi=bars.length;while(lo<hi){const mid=Math.floor((lo+hi)/2);if(bars[mid].start<s.confirmed_at)lo=mid+1;else hi=mid;}const following=bars.slice(lo,lo+10),d=s.direction==='bullish'?1:-1,close=s.close??s.price2;s.forward={};for(const h of [5,10]){const w=following.slice(0,h);s.forward[h]=w.length<h||close===0?{status:'incomplete',candles:w.length}:{status:'complete',candles:h,directional_close_pct:d*(w.at(-1).close/close-1)*100,favorable_pct:Math.max(0,...w.map(b=>d*((d===1?b.high:b.low)/close-1)*100)),adverse_pct:Math.max(0,...w.map(b=>-d*((d===1?b.low:b.high)/close-1)*100))};}}
 return {id:await hash(JSON.stringify([source,strategy,symbol,frame,rules,bars.at(-1).end])),strategy,symbol,timeframe:frame,source,filename:String(body.filename||'upload.csv').slice(0,200),start_at:bars[0].start,end_at:bars.at(-1).end,candles:bars.length,excluded_open_candles:excluded,warmup_candles:strategy==='pin-bars'?rules.slow:minimum,limited_history:bars.length<250,alignment,rules,signals,generated_at:now,signal_count:signals.length,note:'Signal replay and subsequent price movement; no entry/exit rules, trading returns or win rate are defined. Uploaded indicator columns are ignored.'};
}
