import {detect} from '../backtests/engine.mjs';

const day=86400000;
function wall(value,zone){
 const p=Object.fromEntries(new Intl.DateTimeFormat('en-US',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(value).map(p=>[p.type,p.value]));
 return new Date(Date.UTC(+p.year,+p.month-1,+p.day,+p.hour,+p.minute,+p.second,value%1000));
}
function instant(date,zone){let value=date.getTime();for(let i=0;i<4;i++){const delta=date.getTime()-wall(value,zone).getTime();value+=delta;if(!delta)break;}return value;}
function nth(y,m,w,n){const first=new Date(Date.UTC(y,m,1));return 1+(w-first.getUTCDay()+7)%7+(n-1)*7;}
function easter(y){const a=y%19,b=Math.floor(y/100),c=y%100,d=Math.floor(b/4),e=b%4,f=Math.floor((b+8)/25),g=Math.floor((b-f+1)/3),h=(19*a+b-d-g+15)%30,i=Math.floor(c/4),k=c%4,l=(32+2*e+2*i-h-k)%7,m=Math.floor((a+11*h+22*l)/451),v=h+l-7*m+114;return Date.UTC(y,Math.floor(v/31)-1,v%31+1);}
function observed(y,m,d){const t=new Date(Date.UTC(y,m,d)),w=t.getUTCDay();return t.getTime()+(w===6?-day:w===0?day:0);}
function trading(date){
 const y=date.getUTCFullYear(),m=date.getUTCMonth(),d=date.getUTCDate(),w=date.getUTCDay(),value=Date.UTC(y,m,d);
 const ny=new Date(Date.UTC(y,0,1)),newYear=ny.getTime()+(ny.getUTCDay()===0?day:0);
 const holidays=[newYear,Date.UTC(y,0,nth(y,0,1,3)),Date.UTC(y,1,nth(y,1,1,3)),easter(y)-2*day,Date.UTC(y,4,31-((new Date(Date.UTC(y,4,31)).getUTCDay()+6)%7)),observed(y,6,4),Date.UTC(y,8,nth(y,8,1,1)),Date.UTC(y,10,nth(y,10,4,4)),observed(y,11,25),Date.UTC(2025,0,9)];
 if(y>=2022)holidays.push(observed(y,5,19));return w!==0&&w!==6&&!holidays.includes(value);
}
function forexOpen(date){const w=date.getUTCDay(),minutes=date.getUTCHours()*60+date.getUTCMinutes();return w>0&&w<5||w===5&&minutes<1020||w===0&&minutes>=1020;}
export function nextStart(bar,frame,market='crypto'){
 const zone=bar.start%day===0?'UTC':market==='stock'||market==='forex'?'America/New_York':'UTC',start=wall(bar.start,zone),next=new Date(start);
 if(frame==='monthly'){const d=next.getUTCDate();next.setUTCMonth(next.getUTCMonth()+1,1);const last=new Date(Date.UTC(next.getUTCFullYear(),next.getUTCMonth()+1,0)).getUTCDate();next.setUTCDate(Math.min(d,last));}
 else next.setTime(next.getTime()+({'1h':3600000,'4h':14400000,daily:day,weekly:7*day})[frame]);
 if(['1h','4h'].includes(frame)){
  next.setTime(wall(bar.end,zone).getTime());
  if(market==='stock'){
   const thanksgiving=nth(start.getUTCFullYear(),10,4,4),early=(start.getUTCMonth()===10&&start.getUTCDate()===thanksgiving+1)||(start.getUTCMonth()===11&&start.getUTCDate()===24)||(start.getUTCMonth()===6&&start.getUTCDate()===3);
   const close=new Date(start);close.setUTCHours(early?13:16,0,0,0);
   if(next>=close){next.setTime(start.getTime()+day);while(!trading(next))next.setTime(next.getTime()+day);next.setUTCHours(9,30,0,0);}
  }else if(market==='forex'&&!forexOpen(next)){const w=next.getUTCDay();next.setUTCDate(next.getUTCDate()+(7-w)%7);next.setUTCHours(17,0,0,0);}
 }else if(frame==='daily'){
  if(market==='stock')while(!trading(next))next.setTime(next.getTime()+day);
  else if(market==='forex')while(!forexOpen(next)&&!(next.getUTCDay()===0&&next.getUTCHours()<17))next.setTime(next.getTime()+day);
 }
 return instant(next,zone);
}
export function pinbarDirection(b,rules){
 const body=Math.abs(b.open-b.close),lower=Math.min(b.open,b.close)-b.low,upper=b.high-Math.max(b.open,b.close);
 if(lower>rules.ratio*body&&lower>upper&&upper<=lower*rules.max_short_wick_ratio)return 'bullish';
 if(upper>rules.ratio*body&&upper>lower&&lower<=upper*rules.max_short_wick_ratio)return 'bearish';
}
export function detectRSIPB(bars,rules,source,symbol,timeframe,market='crypto'){
 const groups=new Map(),indexed=new Map(bars.map(b=>[b.start,b])),signals=[];
 for(const s of detect(bars,rules.rsi,source,timeframe,true)){
  const key=s.pivot2+':'+s.direction,b=indexed.get(s.pivot2);
  s.pivot1_closed_at=indexed.get(s.pivot1).end;s.pivot2_closed_at=b.end;
  if(!groups.has(key))groups.set(key,{strategy:'rsi-pb',version:rules.version,source,symbol,timeframe,direction:s.direction,divergences:[],pivot2:s.pivot2,pivot_closed_at:b.end,pivot_candle:b,rules});
  groups.get(key).divergences.push(s);
 }
 for(const setup of groups.values()){
  const b=indexed.get(nextStart(setup.pivot_candle,timeframe,market));if(!b)continue;
  const bias=pinbarDirection(b,rules);if(!bias||bias===setup.direction)continue;
  signals.push({...setup,pinbar_direction:bias,candle_at:b.start,confirmed_at:b.end,open:b.open,high:b.high,low:b.low,close:b.close,body:Math.abs(b.open-b.close),lower_wick:Math.min(b.open,b.close)-b.low,upper_wick:b.high-Math.max(b.open,b.close)});
 }
 return signals.sort((a,b)=>a.confirmed_at-b.confirmed_at||a.pivot2-b.pivot2||a.direction.localeCompare(b.direction));
}
