export {followupWindows} from './followup.mjs';
// Mirrors scanner.detect: all eligible close-pivot pairs; only P2 needs a BB touch.
export function detect(candles,rules,symbol,timeframe,provisional=false){
 const n=rules.rsi_period,b=rules.bb_period,left=rules.pivot_left,right=rules.pivot_right;
 const rsi=[],lower=[],upper=[];let gain=0,loss=0;
 for(let i=0;i<candles.length;i++){
  const close=candles[i].close;
  if(i){
   const change=close-candles[i-1].close,g=Math.max(change,0),l=Math.max(-change,0);
   if(i<=n){gain+=g/n;loss+=l/n;}else{gain=(gain*(n-1)+g)/n;loss=(loss*(n-1)+l)/n;}
   if(i>=n)rsi[i]=gain===0&&loss===0?50:loss===0?100:gain===0?0:100-100/(1+gain/loss);
  }
  if(i>=b-1){
   const window=candles.slice(i-b+1,i+1).map(c=>c.close),mean=window.reduce((a,v)=>a+v,0)/b;
   const sigma=Math.sqrt(window.reduce((a,v)=>a+(v-mean)**2,0)/b);
   lower[i]=mean-rules.bb_multiplier*sigma;upper[i]=mean+rules.bb_multiplier*sigma;
  }
 }
 const minimum=Math.max(b+left+right,n+1,rules.max_spacing+left+right+1),signals=[];
 for(const direction of ['bullish','bearish']){
  let references=[];
  const prices=candles.map(c=>c.close);
  const bands=direction==='bullish'?lower:upper;
  const pivot=(j,end)=>prices.slice(j-left,j).concat(prices.slice(j+1,end)).every(v=>direction==='bullish'?prices[j]<v:prices[j]>v);
  for(let i=left;i<(provisional?candles.length:candles.length-right);i++){
   if(provisional){
    const j=i-right;
    if(j>=left&&pivot(j,i+1))references.push(j);
   }
   references=references.filter(j=>i-j<=rules.max_spacing);
   if(!pivot(i,provisional?i+1:i+right+1))continue;
   const touch=direction==='bullish'?candles[i].low:candles[i].high;
   const bandTouch=bands[i]!==undefined&&(direction==='bullish'?touch<=bands[i]:touch>=bands[i]);
   for(const first of references){
   if([rsi[first],rsi[i],bands[first],bands[i]].some(v=>v===undefined))continue;
   const spacing=i-first,event=provisional?i:i+right;
   if(spacing<rules.min_spacing||spacing>rules.max_spacing||event<minimum-1)continue;
   const tolerance=rules.rsi_tolerance??0;
   const improvement=direction==='bullish'?rsi[i]-rsi[first]:rsi[first]-rsi[i];
   const rsiMatch=improvement>0||(tolerance>0&&improvement>=-tolerance);
   const divergence=direction==='bullish'?prices[i]<prices[first]&&rsiMatch:prices[i]>prices[first]&&rsiMatch;
   if(!divergence||!bandTouch)continue;
   const s={symbol,timeframe,direction,price1:prices[first],price2:prices[i],rsi1:rsi[first],rsi2:rsi[i],band1:bands[first],band2:bands[i],band_touch_price:touch,pivot1:candles[first].start,pivot2:candles[i].start,confirmed_at:candles[event].end,spacing,rules};
   if(provisional)Object.assign(s,{signal_status:'provisional',alert_timing:'pivot_close',pivot_closed_at:candles[i].end});
   signals.push(s);
   }
   if(!provisional)references.push(i);
  }
 }
 return signals.sort((a,b)=>a.confirmed_at-b.confirmed_at);
}
export function validateCandles(candles){let end=0;for(const c of candles){if(!Number.isSafeInteger(c.start)||!Number.isSafeInteger(c.end)||c.start<=0||c.end<=c.start||c.start<end||![c.open,c.high,c.low,c.close].every(Number.isFinite)||c.low>Math.min(c.open,c.close)||c.high<Math.max(c.open,c.close))throw Error('Invalid historical OHLC');end=c.end;}return candles;}
