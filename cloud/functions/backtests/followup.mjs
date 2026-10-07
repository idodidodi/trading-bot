// Research outcome from P2; source evidence and user feedback remain independent.
export function calculateFollowup(signal, candles, mode='recovery') {
 if(!['recovery','all'].includes(mode))throw Error('Invalid follow-up window');
 const bars=[...candles].sort((a,b)=>a.start-b.start),start=signal.pivot2,price=signal.price2;
 const result={version:1,mode,baseline_at:start,baseline_price:price,status:'unavailable',rating:null,candles:0};
 if(price<=0||!bars.some(c=>c.start===start))return {...result,reason:'The original pivot candle is missing from stored history.'};
 const future=bars.filter(c=>c.start>start);if(!future.length)return {...result,reason:'No closed candles after the divergence pivot yet.'};
 const bullish=signal.direction==='bullish';let dd=0,gain=0,ddAt=null,gainAt=null,ddPrice=null,gainPrice=null,recoveryAt=null,adverseAt=null,count=0,through=null;
 for(const c of future){count++;through=c.end;const unfavorable=bullish?c.low:c.high,favorable=bullish?c.high:c.low;
  const adverse=Math.max(0,(bullish?price-unfavorable:unfavorable-price)/price*100),favorablePct=Math.max(0,(bullish?favorable-price:price-favorable)/price*100);
  if(adverse>dd){dd=adverse;ddAt=c.end;ddPrice=unfavorable;recoveryAt=null;}if(favorablePct>gain){gain=favorablePct;gainAt=c.end;gainPrice=favorable;}
  if(ddAt!==null&&ddAt<c.end&&recoveryAt===null&&(bullish?c.close>=price:c.close<=price))recoveryAt=c.end;
  if(adverse>0&&adverseAt===null)adverseAt=c.end;
  if(recoveryAt!==null&&mode==='recovery')break;
 }
 const ratio=dd?gain/dd:null,rating=gain===0?1:dd===0||ratio>=3?5:ratio>=2?4:ratio>=1?3:ratio>=.5?2:1;
 return {...result,status:recoveryAt!==null?'recovered':adverseAt!==null?'ongoing':'no drawdown',candles:count,through_at:through,
  drawdown_pct:dd,drawdown_price:ddPrice,drawdown_at:ddAt,drawdown_elapsed_ms:ddAt===null?null:ddAt-start,
  gain_pct:gain,gain_price:gainPrice,gain_at:gainAt,gain_elapsed_ms:gainAt===null?null:gainAt-start,
  recovery_at:recoveryAt,recovery_elapsed_ms:recoveryAt===null?null:recoveryAt-start,gain_drawdown_ratio:ratio,rating};
}
export const followupWindows=(signal,candles)=>Object.fromEntries(['recovery','all'].map(mode=>[mode,calculateFollowup(signal,candles,mode)]));
