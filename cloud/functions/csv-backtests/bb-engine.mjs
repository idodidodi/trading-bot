// BB Pin-Bars is independent of the EMA Pin-Bars strategy.
export function detectBBPinBars(bars,rules,source,symbol,timeframe){
 const signals=[];
 for(let i=rules.bb_period-1;i<bars.length;i++){
  const b=bars[i],window=bars.slice(i+1-rules.bb_period,i+1).map(c=>c.close);
  const middle=window.reduce((sum,v)=>sum+v,0)/rules.bb_period;
  const deviation=Math.sqrt(window.reduce((sum,v)=>sum+(v-middle)**2,0)/rules.bb_period);
  const lowerBB=middle-rules.bb_multiplier*deviation,upperBB=middle+rules.bb_multiplier*deviation;
  const body=Math.abs(b.open-b.close),lower=Math.min(b.open,b.close)-b.low,upper=b.high-Math.max(b.open,b.close);
  const bullish=lower>body*rules.ratio&&lower>upper&&b.low<=lowerBB;
  const bearish=upper>body*rules.ratio&&upper>lower&&b.high>=upperBB;
  if(bullish||bearish)signals.push({strategy:'bb-pin-bars',version:rules.version,source,symbol,timeframe,direction:bullish?'bullish':'bearish',candle_at:b.start,confirmed_at:b.end,open:b.open,high:b.high,low:b.low,close:b.close,lower_bb:lowerBB,upper_bb:upperBB,body,lower_wick:lower,upper_wick:upper,rules});
 }
 return signals;
}
