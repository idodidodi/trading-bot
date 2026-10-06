// One bounded canvas chart, loaded only when the user opens a candle.
// Match scanner.indicators: Wilder RSI and population-deviation Bollinger Bands.
export function indicators(candles,rules){
 const n=rules.rsi_period,b=rules.bb_period,result={rsi:[],lower:[],middle:[],upper:[]};
 let gain=0,loss=0;
 candles.forEach((c,i)=>{
  const time=c.start/1000;
  if(i>0){
   const change=c.close-candles[i-1].close,g=Math.max(change,0),l=Math.max(-change,0);
   if(i<=n){gain+=g/n;loss+=l/n;}else{gain=(gain*(n-1)+g)/n;loss=(loss*(n-1)+l)/n;}
   if(i>=n)result.rsi.push({time,value:gain===0&&loss===0?50:loss===0?100:gain===0?0:100-100/(1+gain/loss)});
  }
  if(i>=b-1){
   const window=candles.slice(i-b+1,i+1).map(c=>c.close),mean=window.reduce((a,v)=>a+v,0)/b;
   const sigma=Math.sqrt(window.reduce((a,v)=>a+(v-mean)**2,0)/b);
   result.middle.push({time,value:mean});
   result.lower.push({time,value:mean-rules.bb_multiplier*sigma});
   result.upper.push({time,value:mean+rules.bb_multiplier*sigma});
  }
 });
 return result;
}
export async function draw(element,candles,signal,target){
 if(!window.LightweightCharts){await new Promise((resolve,reject)=>{const s=document.createElement('script');s.src='/web/vendor/lightweight-charts.js';s.onload=resolve;s.onerror=()=>reject(Error('Chart library unavailable'));document.head.append(s);});}
 const chart=LightweightCharts.createChart(element,{autoSize:true,layout:{background:{color:'#111827'},textColor:'#e5e7eb',attributionLogo:true},grid:{vertLines:{color:'#243044'},horzLines:{color:'#243044'}},timeScale:{timeVisible:true}});
 const series=chart.addSeries(LightweightCharts.CandlestickSeries);series.setData(candles.map(c=>({time:c.start/1000,open:c.open,high:c.high,low:c.low,close:c.close})));
 const values=indicators(candles,signal.rules);
 for(const [key,color,title] of [['upper','#60a5fa','BB upper'],['middle','#fbbf24','BB middle'],['lower','#60a5fa','BB lower']]){
  chart.addSeries(LightweightCharts.LineSeries,{color,title,lineWidth:1,priceLineVisible:false,lastValueVisible:false}).setData(values[key]);
 }
 const rsi=chart.addSeries(LightweightCharts.LineSeries,{color:'#c084fc',title:`RSI (${signal.rules.rsi_period})`,lineWidth:2,priceLineVisible:false,priceFormat:{type:'price',precision:2,minMove:0.01},autoscaleInfoProvider:()=>({priceRange:{minValue:0,maxValue:100}})},1);
 rsi.setData(values.rsi);
 for(const value of [30,70])rsi.createPriceLine({price:value,color:'#64748b',lineWidth:1,lineStyle:LightweightCharts.LineStyle.Dashed,axisLabelVisible:true,title:String(value)});
 chart.panes()[0].setHeight(350);chart.panes()[1].setHeight(150);
 const confirmation=candles.find(c=>c.end===signal.confirmed_at)?.start;
 const targets=[signal.pivot1,signal.pivot2,confirmation].filter(Boolean);
 LightweightCharts.createSeriesMarkers(series,candles.filter(c=>targets.includes(c.start)).map(c=>({time:c.start/1000,position:'aboveBar',color:c.start===signal.pivot2?'#fbbf24':'#67e8f9',shape:'arrowDown',text:c.start===confirmation?(signal.signal_status==='provisional'?'Pivot 2 (provisional)':'Confirmed'):c.start===signal.pivot2?'Pivot 2':'Pivot 1'})));
 const center=target==='confirmation'?confirmation:signal[target];const index=candles.findIndex(c=>c.start===center);
 if(index>=0)chart.timeScale().setVisibleRange({from:candles[Math.max(0,index-30)].start/1000,to:candles[Math.min(candles.length-1,index+30)].start/1000});else chart.timeScale().fitContent();return chart;
}
