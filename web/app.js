const config=window.DASHBOARD_CONFIG||{}, online=Boolean(config.supabaseUrl);
const $=id=>document.getElementById(id),content=$('content'),status=$('status');
const frames=['monthly','weekly','daily','4h','1h'];
const assetFrames=[...frames,'30m','15m','5m'];
const defaultAssetFrames=['monthly','weekly','daily','4h'];
const selectedFindings={live:sessionStorage.getItem('selected_live'),backtest:sessionStorage.getItem('selected_backtest')};
function selectFinding(source,id){selectedFindings[source]=id;sessionStorage.setItem?.('selected_'+source,id);for(const tr of document.querySelectorAll?.('.finding-row')||[]){const active=tr.dataset.findingId===id;tr.classList.toggle('selected-row',active);tr.setAttribute('aria-selected',String(active));}}
function marketCategory(a){if(a.market==='crypto')return 'Crypto';if(a.market==='forex')return 'Forex';if(a.market==='futures')return 'Futures';if(['stock','etf','equity'].includes(a.market)||['NASDAQ','NYSE','AMEX','NYSE ARCA'].includes(a.exchange))return 'Stocks & ETFs';return 'Other assets';}
function coverageWarning(a){const rows=(state.coverage||[]).filter(r=>r.asset===a.id);return rows.filter(r=>/unavailable|insufficient|partial/.test(r.status)).map(r=>r.timeframe+': '+(r.reason||r.status)).join('; ')||a.unavailable_reason||(a.provider==='csv'?'Live feed not connected':'');}
function outcomeCell(r){const n=node('div');r.outcomeElement=n;updateOutcome(r);return n;}
function updateOutcome(r){if(!r.outcomeElement)return;const f=r.followup;r.outcomeElement.replaceChildren();if(!f||f.status==='unavailable'){r.outcomeElement.append(node('small',f?.reason||'Awaiting follow-up'));return;}const gain=node('strong',`Gain ${f.gain_pct.toFixed(2)}%`),dd=node('strong',`Drawdown ${f.drawdown_pct.toFixed(2)}%`);gain.className='success';dd.className='error';r.outcomeElement.append(gain,dd,node('small',f.mode==='all'?'All available history':'Through first recovery'));}
let state,token=sessionStorage.getItem('dashboard_token'),chart=null;
const node=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
function message(text,error=false){status.textContent=text;status.className=error?'error':'';}
let passwordSetupSession=null;
if(online&&location.hash){
 const auth=new URLSearchParams(location.hash.slice(1));
 const code=auth.get('error_code'),description=auth.get('error_description');
 if(code){
  message(code==='otp_expired'?'This sign-in link has expired or was already used. Request a new link below.':(description||'The sign-in link could not be verified.'),true);
  history.replaceState(null,'',location.pathname+location.search);
 }else if(auth.get('access_token')&&auth.get('refresh_token')){
  const session={access_token:auth.get('access_token'),refresh_token:auth.get('refresh_token'),expires_in:Number(auth.get('expires_in'))||3600};
  const type=auth.get('type');
  if(['invite','recovery','signup'].includes(type)){
   passwordSetupSession=session;
   $('invite-setup').hidden=false;
   $('login').hidden=true;
  }else if(['magiclink','email'].includes(type)){
   saveSession(session);
   $('login').hidden=true;
  }
  history.replaceState(null,'',location.pathname+location.search);
 }
}
async function request(path,body,timeout=15000){
 if(online)await refreshSession();
 let url=path,headers={},method=body?'POST':'GET';
 if(online){url=config.supabaseUrl+'/rest/v1/rpc/dashboard_api';method='POST';headers={apikey:config.publishableKey,Authorization:'Bearer '+token};body={installation:config.installationId,operation:path,input:body||{}};}
 if(online&&path==='/api/csv-backtests'&&body.input&&Object.keys(body.input).length){url=config.supabaseUrl+'/functions/v1/csv-backtests';body={installation:config.installationId,input:body.input};}
 if(online&&path==='/api/backtests'&&body.input&&Object.keys(body.input).length){url=config.supabaseUrl+'/functions/v1/backtests';body={installation:config.installationId,input:body.input};}
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),timeout);
 try{const r=await fetch(url,{method,headers:{...headers,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined,signal:controller.signal});const result=await r.json();if(online&&r.status===401){sessionStorage.removeItem('dashboard_token');token=null;$('login').hidden=false;}if(!r.ok||result.error)throw Error(result.error||result.message||'Request failed');return result;}finally{clearTimeout(timer);}
}
let refreshPending=null;
function saveSession(data){token=data.access_token;sessionStorage.setItem('dashboard_token',token);sessionStorage.setItem('dashboard_refresh',data.refresh_token);sessionStorage.setItem('dashboard_expires',String(data.expires_at||Math.floor(Date.now()/1000)+data.expires_in));}
async function refreshSession(){const refresh=sessionStorage.getItem('dashboard_refresh'),expires=Number(sessionStorage.getItem('dashboard_expires'));if(!refresh||expires>Date.now()/1000+60)return;if(refreshPending)return refreshPending;refreshPending=(async()=>{const r=await fetch(config.supabaseUrl+'/auth/v1/token?grant_type=refresh_token',{method:'POST',headers:{apikey:config.publishableKey,'Content-Type':'application/json'},body:JSON.stringify({refresh_token:refresh})});const data=await r.json();if(!r.ok){for(const key of ['dashboard_token','dashboard_refresh','dashboard_expires'])sessionStorage.removeItem(key);token=null;throw Error('Session expired. Please sign in again.');}saveSession(data);})();try{await refreshPending;}finally{refreshPending=null;}}
$('signin').onclick=async()=>{try{const r=await fetch(config.supabaseUrl+'/auth/v1/token?grant_type=password',{method:'POST',headers:{apikey:config.publishableKey,'Content-Type':'application/json'},body:JSON.stringify({email:$('email').value.trim(),password:$('password').value})});const data=await r.json();if(!r.ok)throw Error(data.msg||data.message||'Sign in failed');saveSession(data);$('password').value='';$('login').hidden=true;await load();}catch(e){message(e.message,true);}};
async function sendAuthLink(kind){try{const email=$('email').value.trim();if(!email){$('email').focus();throw Error('Enter your email address first.');}const redirect=encodeURIComponent(location.origin+'/'),recovery=kind==='recovery',path=recovery?'/auth/v1/recover?redirect_to='+redirect:'/auth/v1/otp?redirect_to='+redirect,body=recovery?{email}:{email,create_user:false};const r=await fetch(config.supabaseUrl+path,{method:'POST',headers:{apikey:config.publishableKey,'Content-Type':'application/json'},body:JSON.stringify(body)}),data=await r.json().catch(()=>({}));if(r.status===429)throw Error('Too many authentication emails were requested. Please wait before requesting another link.');if(!r.ok)throw Error(data.msg||data.message||data.error_description||'Could not send the email. Try again in a minute.');message(recovery?'If an account exists for that email, a password reset link is on its way.':'If an account exists for that email, a sign-in link is on its way.');}catch(e){message(e.message,true);}}
$('forgot-password').onclick=()=>sendAuthLink('recovery');
$('magic-link').onclick=()=>sendAuthLink('magiclink');
$('accept-invite').onclick=async()=>{try{if(!passwordSetupSession)throw Error('This password link is no longer active. Request a new one below.');const password=$('invite-password').value,confirm=$('invite-password-confirm').value;if(password.length<8)throw Error('Choose a password with at least 8 characters.');if(password!==confirm)throw Error('The passwords do not match.');const r=await fetch(config.supabaseUrl+'/auth/v1/user',{method:'PUT',headers:{apikey:config.publishableKey,Authorization:'Bearer '+passwordSetupSession.access_token,'Content-Type':'application/json'},body:JSON.stringify({password})});const data=await r.json();if(!r.ok)throw Error(data.msg||data.message||'Could not set the password. Request a new password link and try again.');saveSession(passwordSetupSession);passwordSetupSession=null;$('invite-password').value='';$('invite-password-confirm').value='';$('invite-setup').hidden=true;await load();}catch(e){message(e.message,true);}};
function button(text,fn){const b=node('button',text);b.type='button';b.onclick=fn;return b;}
function csvText(records){
 const flatten=(value,prefix='',row={})=>{for(const [key,item] of Object.entries(value)){const name=prefix?prefix+'.'+key:key;if(item&&typeof item==='object'&&!Array.isArray(item))flatten(item,name,row);else row[name]=Array.isArray(item)?JSON.stringify(item):item;}return row;};
 const rows=records.map(r=>flatten(r)),headers=[...new Set(['symbol','timeframe','direction','confirmed_at',...rows.flatMap(r=>Object.keys(r))])];
 const cell=value=>{let text=value==null?'':String(value);if(typeof value==='string'&&/^[\s]*[=+\-@]/.test(text))text="'"+text;return '"'+text.replace(/"/g,'""')+'"';};
 return '\uFEFF'+[headers.map(cell).join(','),...rows.map(r=>headers.map(h=>cell(r[h])).join(','))].join('\r\n')+'\r\n';
}
function exportCSVButton(filename,getRecords){
 const b=button('Export results to CSV',async()=>{
  b.disabled=true;message('Preparing CSV export…');
  try{const records=await getRecords(),blob=new Blob([csvText(records)],{type:'text/csv;charset=utf-8;'}),url=URL.createObjectURL(blob),link=node('a');link.href=url;link.download=filename.replace(/[^a-z0-9._-]/gi,'_')+'.csv';document.body.append(link);try{link.click();}finally{link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}message(`Exported ${records.length} results to CSV.`);}
  catch(e){message(e.message,true);}finally{b.disabled=false;}
 });b.title='Download all results, including every page. Timestamps are Unix milliseconds.';return b;
}
async function allResultPages(path,key){
 const records=[];
 for(let p=0;p<=10000;p++){const data=await request(path+'&page='+p),result=key==='report'?data.report:data;if(!result)throw Error('Backtest report is no longer available.');const rows=result[key==='report'?'signals':key]||[];records.push(...rows);if(!result.has_more)return records;if(!rows.length)throw Error('Unable to retrieve all backtest results.');}
 throw Error('Backtest export exceeds the supported page limit.');
}
function manageAssetButton(signal){
 const b=button('Manage asset',()=>openAssetManager(signal));
 b.setAttribute('aria-label',`Manage scanner asset ${signal.symbol}`);return b;
}
function assetMatches(signal,assets){
 const exact=assets.filter(a=>a.id===signal.symbol);if(exact.length)return exact;
 const identities=[signal.symbol,signal.source].filter(Boolean);
 const feeds=assets.filter(a=>assetFrames.some(f=>{
  const provider=a.timeframe_providers?.[f]||a.provider;
  const exchange=provider==='kraken'?'Kraken':provider==='tiingo'?'Tiingo-UTC':a.exchange||'configured';
  const identity=`${provider}:${exchange}:${a.symbol||a.id}`;
  return identities.includes(identity)||identities.some(value=>((provider==='oanda'&&value.startsWith('oanda:OANDA-'))||(provider==='alpaca'&&/^alpaca:Alpaca-(sip|iex)-split:/.test(value)))&&value.split(':').slice(2).join(':')===(a.symbol||a.id));
 }));if(feeds.length)return feeds;
 return assets.filter(a=>(a.symbol===signal.symbol||a.tradingview_symbol===signal.symbol)&&(!signal.market||!a.market||a.market===signal.market));
}
let assetManagerGeneration=0;
async function openAssetManager(signal){
 const generation=++assetManagerGeneration,dialog=$('asset-dialog'),panel=$('asset-content');
 $('asset-title').textContent=`Manage asset · ${signal.symbol}`;
 panel.replaceChildren(node('p','Loading scanner settings…'));if(!dialog.open)dialog.showModal();
 const error=node('p');error.className='error';error.setAttribute('role','alert');
 try{
  let settings=await request('/api/assets');if(generation!==assetManagerGeneration||!dialog.open)return;
  const assets=settings.config.assets;
  let matches=assetMatches(signal,assets),newAsset=false,addedDraft=false;
  // Latest scanner coverage preserves the exact provider mapping of rotating selections.
  if(!matches.length){
   const coverage=(settings.coverage||[]).map(r=>({id:r.asset,provider:r.provider,symbol:r.symbol,exchange:r.exchange}));
   const candidates=assetMatches(signal,coverage),ids=[...new Set(candidates.map(a=>a.id))];
   if(ids.length===1){
    const rows=coverage.filter(a=>a.id===ids[0]),base=rows.find(a=>a.provider==='twelvedata')||rows[0];
    const selectionMarket=Object.entries(settings.daily_selection?.selected||{}).find(([,rows])=>rows.some(r=>r.symbol===base.symbol))?.[0];
    if(['twelvedata','kraken','oanda','tiingo','alpaca','fmp','yahoo'].includes(base.provider)&&base.symbol){
     const exchange=base.provider==='oanda'?'OANDA':base.provider==='tiingo'?'Tiingo':base.exchange;
     matches=[{...base,exchange,market:signal.market||selectionMarket||({alpaca:'stock',kraken:'crypto',oanda:'forex',tiingo:'forex'})[base.provider],enabled:true,feed_confirmed:true,timeframes:[...new Set(settings.coverage.filter(r=>r.asset===base.id).map(r=>r.timeframe))],timeframe_providers:Object.fromEntries(settings.coverage.filter(r=>r.asset===base.id&&r.provider!==base.provider).map(r=>[r.timeframe,r.provider])),scan_strategies:settings.coverage.find(r=>r.asset===base.id)?.scan_strategies||['rsi','pin-bars','rsi-pb'],tradingview_symbol:''}];newAsset=true;
    }
   }
  }
  if(!matches.length&&signal.strategy==='momentum'&&signal.market==='stock'){
   matches=[{id:'STOCK-'+signal.symbol,provider:'alpaca',symbol:signal.symbol,exchange:'Alpaca-sip-split',market:'stock',enabled:true,feed_confirmed:true,timeframes:['daily'],scan_strategies:['rsi','pin-bars','rsi-pb'],tradingview_symbol:''}];newAsset=true;
  }
  panel.replaceChildren();
  if(!matches.length){
   panel.append(node('p','This signal does not have a current scanner asset mapping. Add and configure its exact data feed in Assets before managing scanner timeframes.'),button('Open Assets',()=>location.assign('/assets')),error);return;
  }
  if(matches.length>1){
   const picker=node('select');picker.setAttribute('aria-label','Scanner asset');
   const placeholder=node('option','Choose the scanner asset');placeholder.value='';picker.append(placeholder);
   for(const a of matches){const o=node('option',`${a.id} · ${a.provider} · ${a.exchange||''}`);o.value=a.id;picker.append(o);}
   const editor=node('div');panel.append(node('p','Several scanner assets use this symbol. Choose which asset to edit.'),picker,editor);
   picker.onchange=()=>{editor.replaceChildren();const a=matches.find(a=>a.id===picker.value);if(a)renderEditor(a,editor);};
  }else renderEditor(matches[0],panel);
  function renderEditor(asset,target){
   $('asset-title').textContent=`Manage asset · ${asset.id}`;
   const form=node('form'),enabled=node('input');enabled.type='checkbox';enabled.checked=asset.enabled!==false;
   const enableLabel=node('label','Enabled in scanner');enableLabel.append(enabled);
   form.append(node('p',`${asset.id} · ${asset.provider} · ${asset.symbol||asset.id}`),enableLabel);
   const field=node('fieldset');field.append(node('legend','Scanner timeframes'));
   const selected={};for(const f of assetFrames){const label=node('label',({monthly:'Monthly',weekly:'Weekly',daily:'Daily','4h':'4 hours','1h':'1 hour','30m':'30 minutes','15m':'15 minutes','5m':'5 minutes'})[f]),input=node('input');input.type='checkbox';input.value=f;input.checked=(asset.timeframes||settings.config.timeframes||defaultAssetFrames).includes(f);selected[f]=input;label.append(input);field.append(label);}
   form.append(field,node('p','Select one or more timeframes. Uncheck a timeframe to remove it, or replace it by selecting another. These settings apply across this asset’s candle strategies. Momentum always uses daily candles.'));
   const strategyField=node('fieldset');strategyField.append(node('legend','Scan strategies'));
   const strategyInputs={},strategyOptions=[['rsi','RSI divergence'],['pin-bars','Pin-Bars'],['bb-pin-bars','BB Pin-Bars'],['rsi-pb','RSI + PB']];
   for(const [key,title] of strategyOptions){const label=node('label',title),input=node('input');input.type='checkbox';input.value=key;input.checked=(asset.scan_strategies||['rsi','pin-bars','rsi-pb']).includes(key);strategyInputs[key]=input;label.append(input);strategyField.append(label);}
   form.append(strategyField,node('p','Selected strategies run on every selected timeframe for this asset. Momentum is managed separately by the daily scanner.'));
   if(newAsset)form.append(node('p','Saving keeps this rotating selection in your permanent assets so its scanner settings persist.'));
   if(asset.provider==='csv')form.append(node('p','Each selected timeframe needs its own configured candle CSV file.'));
   const save=node('button','Save changes');save.type='submit';
   const actions=node('div');actions.className='asset-modal-actions';actions.append(save,button('Cancel',()=>dialog.close()));form.append(error,actions);target.append(form);
   form.onsubmit=async event=>{
    event.preventDefault();const timeframes=assetFrames.filter(f=>selected[f].checked);
    if(!timeframes.length){error.textContent='Choose at least one timeframe. To pause all scanning, turn off Enabled in scanner.';return;}
    const scan_strategies=strategyOptions.map(([key])=>key).filter(key=>strategyInputs[key].checked);
    if(!scan_strategies.length){error.textContent='Choose at least one scan strategy. To pause all scanning, turn off Enabled in scanner.';return;}
    save.disabled=true;error.textContent='';
    try{
     if(newAsset){
      settings=await request('/api/assets',{action:'bulk',tickers:asset.id,revision:settings.revision});
      if(!settings.config.assets.some(a=>a.id===asset.id))throw Error('Could not add the asset to the scanner.');
      newAsset=false;addedDraft=true;
     }
     const result=await request('/api/assets',{action:'save',revision:settings.revision,asset:{...asset,enabled:enabled.checked,timeframes,scan_strategies}});
     settings=result;state=result;
     if(generation===assetManagerGeneration){dialog.close();message(`${asset.id}: scanner ${enabled.checked?'enabled':'disabled'} · ${timeframes.join(', ')}${online?' — pending local scanner':' — saved locally'}`);}
    }catch(e){error.textContent=e.message+(addedDraft?' A disabled draft was added in Assets, but these settings were not saved.':' Your changes have not been applied.')+' Close and reopen this dialog to reload the latest settings.';}finally{save.disabled=false;}
   };
  }
 }catch(e){if(generation===assetManagerGeneration&&dialog.open){error.textContent=e.message;panel.replaceChildren(error,button('Retry',()=>openAssetManager(signal)));}}
}
$('close-asset').onclick=()=>$('asset-dialog').close();
$('asset-dialog').addEventListener('close',()=>{assetManagerGeneration++;$('asset-dialog').className='';});
async function loadColmexRequests(parent){
 const section=node('section');section.className='colmex-requests';parent.append(section);
 section.append(node('h3','Colmex MT4 · requested forex pairs'));
 if(!online){section.append(node('p','Sign in to the hosted dashboard to manage your personal Colmex requests.'));return;}
 try{
  const value=await request('/api/colmex'),feed=value.feed||{},age=Date.now()/1000-Number(feed.updated_at||0);
  const fresh=feed.status==='collecting'&&age>=-5&&age<=120;
  section.append(node('p',fresh?'Terminal connected · catalogue available':'Feed unavailable or stale · '+(feed.reason||'waiting for terminal update')),
   node('p','Choose up to 10 exact broker pairs. Requests are shared across subscribers; collection does not mean candles are ready to scan. Removing your request keeps pairs needed by others.'),
   node('p',value.note||''));
  const names=[...new Set([...(value.symbols||[]),...(fresh?(feed.catalogue||[]).filter(s=>s.export_supported&&s.forex===true).map(s=>s.symbol):[])])].sort();
  const form=node('form'),inputs={},feedback=node('p');
  for(const name of names){const label=node('label',name),input=node('input');input.type='checkbox';input.checked=(value.symbols||[]).includes(name);inputs[name]=input;label.append(input);form.append(label);}
  const save=node('button','Save my requested pairs');form.append(save,feedback);
  form.onsubmit=async event=>{event.preventDefault();const symbols=Object.keys(inputs).filter(s=>inputs[s].checked);if(symbols.length>10){feedback.textContent='Choose at most 10 pairs.';return;}save.disabled=true;
   try{const result=await request('/api/colmex',{action:'set',revision:value.revision,symbols});value.revision=result.revision;feedback.textContent='Saved. Collection and scanning will update when the worker validates the feed.';}
   catch(error){feedback.textContent=error.message+' Reload before retrying.';}finally{save.disabled=false;}
  };section.append(form);
  if(feed.owner_disabled_symbols?.length)section.append(node('p','Disabled by the installation owner: '+feed.owner_disabled_symbols.join(', ')));
  const assets=feed.terminal?.assets||[];
  if(assets.length)section.append(table(['Broker symbol','Collection'],assets.map(a=>[a.symbol,a.status])));
  const coverage=(value.coverage||[]).filter(r=>r.provider==='colmex');
  if(coverage.length)section.append(table(['Symbol','Timeframe','Scanner status','Details'],coverage.map(r=>[r.symbol||r.asset,r.timeframe,r.status,r.reason||r.data_notes||''])));
  else section.append(node('p','Scanner readiness has not been checked yet.'));
 }catch(error){section.append(node('p','Colmex requests unavailable: '+error.message));}
}
// Asset setup owns feed defaults; users select instruments, timeframes and strategies.
const assetProviderNames={csv:'CSV files',twelvedata:'Twelve Data',kraken:'Kraken',oanda:'OANDA',tiingo:'Tiingo',alpaca:'Alpaca',fmp:'FMP',yahoo:'Yahoo (delayed)',colmex:'Colmex MT4'};
const assetFrameNames={monthly:'Monthly',weekly:'Weekly',daily:'Daily','4h':'4 hours','1h':'1 hour','30m':'30 minutes','15m':'15 minutes','5m':'5 minutes'};
// Exact supported instruments from yahoo_feed.INSTRUMENTS and tiingo_feed.validate_feed.
const otherAssetInstruments=[
 {symbol:'^GSPC',name:'S&P 500 cash index',market:'index',provider:'yahoo',exchange:'SNP'},
 {symbol:'^GDAXI',name:'DAX cash index',market:'index',provider:'yahoo',exchange:'XETRA'},
 {symbol:'DX-Y.NYB',name:'US Dollar Index',market:'index',provider:'yahoo',exchange:'NYB'},
 {symbol:'ES=F',name:'E-mini S&P 500 futures',market:'futures',provider:'yahoo',exchange:'CME'},
 {symbol:'CL=F',name:'WTI crude oil futures',market:'futures',provider:'yahoo',exchange:'NYMEX'},
 {symbol:'XAU/USD',name:'Gold / USD',market:'commodity',provider:'tiingo',exchange:'Tiingo'},
 {symbol:'USOIL/USD',name:'WTI oil / USD',market:'commodity',provider:'tiingo',exchange:'Tiingo'},
];
function assetFrameAvailable(asset,frame){
 const provider=asset.timeframe_providers?.[frame]||asset.provider;
 if(provider==='csv')return true;
 if(['30m','15m','5m'].includes(frame))return provider==='kraken';
 if(provider==='kraken')return frame!=='monthly';
 if(provider==='fmp')return ['monthly','weekly','daily'].includes(frame);
 if(provider==='colmex')return ['daily','4h','1h'].includes(frame);
 return frames.includes(frame);
}
function assetTimeframeControls(asset){
 const field=node('fieldset'),inputs={};field.className='asset-choices';field.append(node('legend','Timeframes'));
 for(const f of assetFrames){
  const label=node('label',assetFrameNames[f]),input=node('input');input.type='checkbox';input.value=f;input.checked=(asset.timeframes||defaultAssetFrames).includes(f);
  // Keep existing selections removable, including legacy unsupported selections.
  input.disabled=!assetFrameAvailable(asset,f)&&!input.checked;
  if(!assetFrameAvailable(asset,f)){label.title='Not available with this asset’s data connection';label.append(node('small','Unavailable'));}
  label.append(input);field.append(label);inputs[f]=input;
 }
 return {field,inputs};
}
function assetStrategyControls(asset){
 const field=node('fieldset'),inputs={};field.className='asset-choices';field.append(node('legend','Scan strategies'));
 for(const [key,title] of [['rsi','RSI divergence'],['pin-bars','Pin-Bars'],['bb-pin-bars','BB Pin-Bars'],['rsi-pb','RSI + PB']]){
  const label=node('label',title),input=node('input');input.type='checkbox';input.value=key;input.checked=(asset.scan_strategies||['rsi','pin-bars','rsi-pb']).includes(key);input.setAttribute('aria-label',asset.id+' '+title);label.append(input);field.append(label);inputs[key]=input;
 }
 return {field,inputs};
}
function assetDataStatus(asset,settings){
 if(asset.provider==='csv')return 'CSV files · live data not configured';
 if(settings.revision!==settings.applied_revision)return 'Awaiting scanner data check';
 const rows=(settings.coverage||[]).filter(r=>r.asset===asset.id&&r.symbol===(asset.symbol||asset.id)&&(asset.timeframes||defaultAssetFrames).includes(r.timeframe)&&r.provider===(asset.timeframe_providers?.[r.timeframe]||asset.provider));
 if(rows.some(r=>/unavailable|insufficient|partial/.test(r.status)))return 'Data needs attention';
 return rows.length&&(asset.timeframes||defaultAssetFrames).every(f=>rows.some(r=>r.timeframe===f&&r.last_closed_at))?'Candle data received · see latest checks below':'Awaiting scanner data check';
}
function automaticAsset(ticker,category,instrument=''){
 const id=ticker.trim().toUpperCase();
 if(!/^[A-Z0-9_^=:.!/\-]{1,100}$/.test(id))throw Error('Enter one asset symbol, such as MU, EURUSD or BTC/USD.');
 let mapping;
 if(category==='stock'){
  if(!/^[A-Z][A-Z0-9.\-]{0,14}$/.test(id))throw Error('Enter the US-listed stock or ETF ticker, without an exchange prefix.');
  mapping={symbol:id,market:'stock',provider:'alpaca',exchange:''};
 }else if(category==='forex'||category==='crypto'){
  let symbol=id.replace('_','/');
  if(!symbol.includes('/')){
   const quote=['USDT','USDC','USD','EUR','GBP','JPY','CHF','CAD','AUD','NZD','BTC','ETH'].find(q=>symbol.endsWith(q)&&symbol.length>q.length);
   if(quote)symbol=symbol.slice(0,-quote.length)+'/'+quote;
  }
  if(!(category==='forex'?/^[A-Z]{3}\/[A-Z]{3}$/:/^[A-Z0-9]+\/[A-Z0-9]+$/).test(symbol)||symbol.split('/')[0]===symbol.split('/')[1])throw Error('Enter the exact pair including its quote currency, such as '+(category==='forex'?'EUR/USD.':'SOL/USD or SOL/USDT.'));
  mapping={symbol,market:category,provider:category==='forex'?'oanda':'kraken',exchange:category==='forex'?'OANDA':'Kraken'};
 }else if(category==='other'){
  mapping=otherAssetInstruments.find(a=>a.symbol===(instrument||id));
  if(!mapping)throw Error('Choose the exact supported instrument. If it is not listed, its data connection needs to be added first.');
 }else throw Error('What category is this asset? Choose Stock / ETF, Crypto, Forex or Other.');
 const asset={...mapping,id,enabled:true,feed_confirmed:true,tradingview_symbol:'',timeframe_providers:{},scan_strategies:['rsi','pin-bars','rsi-pb']};
 asset.timeframes=defaultAssetFrames.filter(f=>assetFrameAvailable(asset,f));
 return asset;
}
function inferredAssetCategory(ticker){
 const symbol=ticker.toUpperCase().replace('_','/');
 if(otherAssetInstruments.some(a=>a.symbol===symbol))return 'other';
 const compact=symbol.replace('/',''),currencies=['USD','EUR','GBP','JPY','CHF','CAD','AUD','NZD'];
 if(compact.length===6&&currencies.includes(compact.slice(0,3))&&currencies.includes(compact.slice(3))&&compact.slice(0,3)!==compact.slice(3))return 'forex';
 if(/^(BTC|ETH|XBT)\/?(USD|USDT|USDC|EUR|GBP)$/.test(symbol))return 'crypto';
 return '';
}
// Bundled lookup snapshot: stock_universe.json (2026-10-08), common ETFs and currency/spot pairs.
// Additional listing: CRCL, verified at https://investor.circle.com/stock-info/default.aspx (2026-11-06).
// Suggestions are instrument identities, not a claim of provider access or current index membership.
const cachedStockSymbols="A AA AAL AAON AAPL ABBV ABNB ABT ACGL ACI ACM ACN ADBE ADC ADI ADM ADP ADSK AEE AEIS AEP AES AFG AFL AGCO AGNC AHR AIG AIT AIZ AJG AKAM ALB ALGM ALGN ALK ALL ALLE ALLY ALSN ALV AM AMAT AMCR AMD AME AMG AMGN AMH AMKR AMP AMT AMZN AN ANET ANF AON AOS APA APD APG APH APO APP APPF APTV AR ARE ARES ARKK ARMK ARW ARWR ASB ASH ATI ATO ATR AVAV AVGO AVNT AVT AVTR AVY AWK AXON AXP AXTA AYI AZO BA BAC BAH BALL BAX BBWI BBY BC BCO BDC BDX BE BEN BF.B BG BHF BIIB BILL BIO BJ BKH BKNG BKR BLK BMRN BMY BNY BR BRK.B BRKR BRO BROS BRX BSX BSY BTSG BURL BWA BWXT BX BXP BYD C CACI CAH CAR CARR CART CASY CAT CAVA CB CBOE CBRE CBSH CBT CCI CCK CCL CDE CDNS CDP CDW CEG CELH CF CFG CFR CG CGNX CHD CHDN CHE CHH CHRD CHRW CHTR CHWY CI CIEN CINF CL CLF CLH CLX CMC CMCSA CME CMG CMI CMS CNC CNH CNM CNO CNP CNX COF COHR COIN COKE COLB COLM COO COP COR CORT COST CPAY CPRT CPT CR CRBG CRCL CRH CRL CRM CROX CRS CRUS CRWD CSCO CSGP CSL CSX CTAS CTRE CTSH CUBE CUZ CVLT CVNA CVS CVX CW CXT CYTK D DAL DAR DASH DBX DCI DD DDOG DE DECK DELL DG DGX DHI DHR DIA DINO DIS DKS DLB DLR DLTR DOC DOCN DOCS DOCU DOV DOW DPZ DRI DT DTE DTM DUK DUOL DVA DVN DXCM DY EAT EBAY ECHO ECL ED EEFT EFX EG EGP EHC EIX EL ELAN ELF ELS ELV EME EMR ENS ENSG ENTG EOG EPR EQH EQIX EQT ERIE ES ESAB ESNT ESS ETN ETR EVR EVRG EW EWBC EXC EXE EXEL EXLS EXP EXPD EXPE EXPO EXR F FAF FANG FAST FBIN FCFS FCN FCX FDS FDX FDXF FE FERG FFIN FFIV FHI FHN FICO FIS FISV FITB FIVE FIX FLEX FLG FLR FLS FN FNB FND FNF FOUR FOX FOXA FR FRT FSLR FTI FTNT FTV G GAP GATX GBCI GD GDDY GE GEF GEHC GEN GEV GGG GHC GILD GIS GL GLD GLPI GLW GM GME GMED GNRC GNTX GOOG GOOGL GPC GPK GPN GRMN GS GWRE GWW GXO H HAE HAL HALO HAS HBAN HCA HD HGV HIG HII HIMS HL HLI HLNE HLT HOG HOMB HON HONA HOOD HPE HPQ HQY HR HRB HRL HSIC HST HSY HUBB HUBS HUM HWC HWM HXL IBKR IBM IBOC ICE IDA IDCC IDXX IESC IEX IFF ILMN INCY INGR INTC INTU INVH IP IPGP IQV IR IRM IRT ISRG IT ITT ITW IVZ IWM J JAZZ JBHT JBL JCI JEF JKHY JLL JNJ JPM KBH KBR KD KDP KEX KEY KEYS KHC KIM KKR KLAC KMB KMI KNF KNSL KNX KO KR KRC KRG KRYS KTOS KVUE L LAD LAMR LDOS LEA LECO LEN LFUS LH LHX LII LIN LITE LIVN LLY LMT LNT LNTH LOPE LOW LPX LRCX LSCC LSTR LULU LUV LVS LYB LYV M MA MAA MANH MAR MAS MAT MCD MCHP MCK MCO MDLZ MDT MEDP MET META MGM MIDD MKC MKSI MLI MLM MMM MMS MNST MO MOG.A MOH MORN MOS MP MPC MPWR MRK MRNA MRSH MRVL MS MSA MSCI MSFT MSI MSM MTB MTD MTDR MTG MTN MTSI MTZ MU MUR MUSA MZTI NBIX NCLH NDAQ NDSN NEE NEM NEU NFG NFLX NI NJR NKE NLY NNN NOC NOV NOVT NOW NRG NSC NTAP NTNX NTRS NUE NVDA NVR NVST NVT NWE NWS NWSA NXPI NXST NXT NYT O OC ODFL OGE OGS OHI OKE OKTA OLED OLLI OLN OMC ON ONB ONTO OPCH ORA ORCL ORI ORLY OSK OTIS OVV OXY OZK P PAG PANW PATH PAYX PB PBF PCAR PCG PCTY PEG PEGA PEN PEP PFE PFG PFGC PG PGR PH PHM PII PINS PK PKG PLD PLNT PLTR PM PNC PNFP PNR PNW PODD POR POST PPC PPG PPL PR PRI PRU PSA PSN PSX PTC PVH PWR PYPL Q QCOM QLYS QQQ R RBA RBC RCL RDDT REG REGN REXR RF RGA RGEN RGLD RH RJF RL RLI RMBS RMD RNR ROIV ROK ROKU ROL ROP ROST RPM RRC RRX RS RSG RTX RVTY RYAN RYN SAIA SAIC SANM SARO SBAC SBRA SBUX SCHW SCI SEIC SF SFM SGI SHC SHW SIGI SIRI SITM SJM SKYD SLAB SLB SLGN SLM SLV SMCI SMG SMTC SN SNA SNDK SNPS SNX SO SOLS SOLV SON SPG SPGI SPXC SPY SR SRE SSB SSD ST STAG STE STLD STRL STT STWD STX STZ SUI SW SWK SWKS SWX SYF SYK SYNA SYY T TCBI TDG TDY TECH TEL TER TEX TFC TGT THC THG THO TJX TKO TKR TLN TLT TMO TMUS TNL TOL TOST TPL TPR TREX TRGP TRMB TROW TRU TRV TSCO TSLA TSN TT TTC TTEK TTMI TTWO TWLO TXN TXNM TXRH TXT TYL UAL UBER UBSI UDR UFPI UGI UHS ULS ULTA UMBF UNH UNM UNP UPS URI USB USFD UTHR V VAL VC VEEV VFC VIAV VICI VICR VLO VLTO VLY VMC VMI VMRK VNO VNOM VNT VOO VOYA VRSK VRSN VRT VRTX VST VTI VTR VTRS VVV VYLR VZ WAB WAL WAT WCC WDAY WDC WEC WELL WEX WFC WFRD WH WHR WING WLK WM WMB WMG WMS WMT WPC WRB WSM WSO WST WTFC WTRG WTS WTW WWD WY WYNN XEL XOM XPO XRAY XYL XYZ YETI YUM ZBH ZBRA ZION ZTS".split(' ');
const cachedStockNames={"CRCL":"Circle Internet Group","MU": "Micron Technology", "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "NVIDIA", "AMZN": "Amazon", "GOOGL": "Alphabet Class A", "GOOG": "Alphabet Class C", "META": "Meta Platforms", "TSLA": "Tesla", "AMD": "Advanced Micro Devices", "INTC": "Intel", "NFLX": "Netflix", "SPY": "SPDR S&P 500 ETF", "QQQ": "Invesco QQQ ETF", "IWM": "iShares Russell 2000 ETF", "DIA": "SPDR Dow Jones ETF", "VTI": "Vanguard Total Stock Market ETF", "VOO": "Vanguard S&P 500 ETF", "GLD": "SPDR Gold Shares ETF", "SLV": "iShares Silver Trust ETF", "TLT": "iShares 20+ Year Treasury Bond ETF", "ARKK": "ARK Innovation ETF"};
const cachedAssetCatalogue=[
 ...cachedStockSymbols.map(symbol=>({symbol,name:cachedStockNames[symbol]||symbol,category:'stock'})),
 ...["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "USD/CAD", "NZD/USD", "EUR/GBP", "EUR/JPY", "GBP/JPY", "EUR/CHF", "EUR/AUD", "EUR/CAD", "EUR/NZD", "GBP/CHF", "GBP/AUD", "GBP/CAD", "GBP/NZD", "AUD/JPY", "AUD/NZD", "AUD/CAD", "AUD/CHF", "CAD/JPY", "CAD/CHF", "CHF/JPY", "NZD/JPY", "NZD/CAD", "NZD/CHF", "USD/CNY", "USD/SGD", "USD/HKD", "USD/MXN", "USD/ZAR"].map(symbol=>({symbol,name:symbol.replace('/',' / '),category:'forex'})),
 ...[["BTC", "Bitcoin"], ["ETH", "Ethereum"], ["SOL", "Solana"], ["DOGE", "Dogecoin"], ["NEAR", "NEAR Protocol"], ["FET", "Fetch.ai"], ["XRP", "XRP"], ["ADA", "Cardano"], ["AVAX", "Avalanche"], ["LINK", "Chainlink"], ["DOT", "Polkadot"], ["LTC", "Litecoin"]].flatMap(([base,name])=>['USD','USDT'].map(quote=>({symbol:base+'/'+quote,name:name+' / '+quote,category:'crypto'}))),
 ...otherAssetInstruments.map(a=>({...a,category:'other'})),
];
function assetSuggestions(query,category,assets=[]){
 const normalize=value=>String(value||'').toUpperCase().replace(/[^A-Z0-9]/g,'');
 const wanted=normalize(query),owned=new Set(assets.flatMap(a=>[normalize(a.id),normalize(a.symbol)]).filter(Boolean));
 return cachedAssetCatalogue.filter(a=>(!category||a.category===category)&&(!wanted||normalize(a.symbol).includes(wanted)||normalize(a.name).includes(wanted)))
  .sort((a,b)=>(normalize(b.symbol)===wanted)-(normalize(a.symbol)===wanted)||Number(normalize(b.symbol).startsWith(wanted))-Number(normalize(a.symbol).startsWith(wanted))||a.symbol.localeCompare(b.symbol))
  .slice(0,20).map(a=>({...a,alreadyAdded:owned.has(normalize(a.symbol))}));
}
const assetStrategyNames={'rsi':'RSI divergence','pin-bars':'Pin-Bars','bb-pin-bars':'BB Pin-Bars','rsi-pb':'RSI + PB'};
const assetLibrary={category:'all',query:'',view:'portfolio'};
let assetPageGeneration=0;
function assetGroup(asset){const group=marketCategory(asset);return group==='Stocks & ETFs'?'stock':group==='Crypto'?'crypto':group==='Forex'?'forex':'other';}
function assetHealth(asset,settings){
 if(asset.provider==='csv'&&!asset.enabled&&!asset.feed_confirmed)return {label:'Needs setup',tone:'attention',detail:'Choose a category to connect this asset.'};
 if(asset.enabled===false)return {label:'Paused',tone:'neutral',detail:'Scanning is paused. Your settings are saved.'};
 if(asset.provider==='csv')return {label:'File data',tone:'neutral',detail:'Uses your configured candle files.'};
 const status=assetDataStatus(asset,settings);
 if(status==='Data needs attention')return {label:'Check data',tone:'attention',detail:'The latest data check needs attention. Open the asset for details.'};
 if(status.startsWith('Candle data received'))return {label:'Data received',tone:'ready',detail:'Candle data is available for the selected timeframes.'};
 return {label:'Checking data',tone:'pending',detail:'Waiting for the scanner to check this connection.'};
}
function assetBadge(label,tone='neutral'){const badge=node('span',label);badge.className='library-badge '+tone;return badge;}
function assetConnectionDetails(asset,settings){
 const detail=node('details');detail.className='library-diagnostics';detail.append(node('summary','Data connection details'));
 detail.append(node('p',`Source: ${assetProviderNames[asset.provider]||asset.provider} · ${asset.symbol||asset.id}`));
 if(asset.exchange)detail.append(node('p','Exchange: '+asset.exchange));
 if(asset.tradingview_symbol)detail.append(node('p','Chart: '+asset.tradingview_symbol));
 if(asset.provider==='csv')detail.append(node('p','Files: '+(asset.path||'data/candles/{asset}-{timeframe}.csv')));
 const rows=(settings.coverage||[]).filter(r=>r.asset===asset.id);
 if(!rows.length)detail.append(node('p','No scanner check recorded yet.'));
 for(const row of rows)detail.append(node('p',`${assetFrameNames[row.timeframe]||row.timeframe}: ${row.status}${row.last_closed_at?' · last candle '+displayTime(row.last_closed_at):''}${row.reason?' · '+row.reason:''}`));
 if(asset.unavailable_reason)detail.append(node('p',asset.unavailable_reason));
 return detail;
}
async function openAssetSetup(ticker='',existingId=null){
 const generation=++assetManagerGeneration,dialog=$('asset-dialog'),panel=$('asset-content');dialog.className='asset-workflow';
 if(!dialog.open)dialog.showModal();$('asset-title').textContent=existingId?'Edit asset':'Add an asset';panel.replaceChildren(node('p','Loading asset settings…'));
 const active=()=>generation===assetManagerGeneration&&dialog.open;
 try{
  let settings=await request('/api/assets');if(!active())return;
  const existing=existingId?settings.config.assets.find(a=>a.id===existingId):null;
  if(existingId&&!existing)throw Error('This asset was removed. Refresh the page.');
  let draft=existing?{...existing,timeframes:[...(existing.timeframes||defaultAssetFrames)],scan_strategies:[...(existing.scan_strategies||['rsi','pin-bars','rsi-pb'])]}:null;
  let step=existing&&!(existing.provider==='csv'&&!existing.enabled&&!existing.feed_confirmed)?2:1,category=inferredAssetCategory(ticker),symbol=ticker.trim().toUpperCase(),instrument='',addedDraft=false,busy=false,converted=false,categoryChosen=false;
  function render(){
   const form=node('form');form.className='library-form';const error=node('p');error.className='error';error.setAttribute('role','alert');
   const progress=node('p',existing?'ASSET SETTINGS':step===1?'STEP 1 OF 2 · IDENTIFY':'STEP 2 OF 2 · SCANNING');progress.className='library-kicker';form.append(progress);
   const controls=[];let selected,strategyInputs,enabled,symbolInput,other;
   if(step===1){
    form.append(node('h3','Find an asset to add'),node('p','Choose a category, then search the cached list by symbol or name.'));
    const categories=node('fieldset');categories.className='library-categories';categories.append(node('legend','Asset category'));
    const categoryButtons=[];
    for(const [value,title,subtitle] of [['stock','Stock / ETF','US-listed'],['crypto','Crypto','Spot pairs'],['forex','Forex','Currency pairs'],['other','Other','Indices, futures & commodities']]){
     const choice=button('',()=>{category=value;categoryChosen=true;instrument='';other.value='';refreshCategory();symbolInput.focus?.();symbolInput.select?.();showSuggestions();});choice.onmousedown=event=>event.preventDefault();choice.append(node('strong',title),node('small',subtitle));choice.setAttribute('aria-label',title);choice.dataset.category=value;categories.append(choice);categoryButtons.push(choice);controls.push(choice);
    }
    form.append(categories);
    const label=node('label','Search assets to add');label.className='library-input-label';symbolInput=node('input');symbolInput.value=symbol;symbolInput.placeholder='Type a symbol or name…';symbolInput.maxLength=100;symbolInput.required=true;symbolInput.autocomplete='off';symbolInput.setAttribute('aria-label','Asset symbol');symbolInput.setAttribute('role','combobox');symbolInput.setAttribute('aria-autocomplete','list');symbolInput.setAttribute('aria-controls','asset-suggestions');symbolInput.setAttribute('aria-expanded','false');label.append(symbolInput);
    const lookup=node('div');lookup.className='asset-lookup';const suggestions=node('div');suggestions.id='asset-suggestions';suggestions.className='asset-suggestions';suggestions.setAttribute('role','listbox');suggestions.setAttribute('aria-label','Cached asset suggestions');suggestions.hidden=true;lookup.append(label,suggestions);form.append(lookup);controls.push(symbolInput);
    const hint=node('p');hint.className='library-help';hint.setAttribute('aria-live','polite');form.append(hint);
    other={value:instrument||otherAssetInstruments.find(a=>a.symbol===symbol)?.symbol||''};let matches=[],activeIndex=-1;
    function closeSuggestions(){suggestions.hidden=true;symbolInput.setAttribute('aria-expanded','false');symbolInput.removeAttribute?.('aria-activedescendant');activeIndex=-1;}
    function selectSuggestion(item){
     if(item.alreadyAdded){error.textContent='Already in your assets. Use the management section below to edit it.';return;}
     symbol=item.symbol;symbolInput.value=symbol;category=item.category;categoryChosen=true;instrument=category==='other'?item.symbol:'';other.value=instrument;refreshCategory();closeSuggestions();symbolInput.focus?.();closeSuggestions();
    }
    function showSuggestions(){
     matches=assetSuggestions(symbolInput.value,category,settings.config.assets.filter(a=>a.id!==existingId));activeIndex=-1;suggestions.replaceChildren();symbolInput.removeAttribute?.('aria-activedescendant');
     matches.forEach((item,index)=>{const option=button('',()=>selectSuggestion(item));option.id='asset-suggestion-'+index;option.setAttribute('role','option');option.setAttribute('aria-selected','false');option.setAttribute('aria-disabled',String(item.alreadyAdded));option.tabIndex=-1;option.onmousedown=e=>e.preventDefault();option.append(node('strong',item.symbol),node('span',item.name),node('small',item.alreadyAdded?'Already added':({stock:'Stock / ETF',crypto:'Crypto',forex:'Forex',other:'Other'})[item.category]));suggestions.append(option);});
     if(!matches.length)suggestions.append(node('p','No cached matches. You can enter an exact symbol and choose its category.'));
     suggestions.hidden=false;symbolInput.setAttribute('aria-expanded','true');
    }
    function refreshCategory(){for(const choice of categoryButtons)choice.setAttribute('aria-pressed',String(choice.dataset.category===category));hint.textContent=category?'Suggestions stay within this category. Data availability is checked after saving.':'Search all categories, or choose one to narrow the list.';error.textContent='';}
    symbolInput.oninput=()=>{symbol=symbolInput.value.trim();instrument='';other.value='';if(!categoryChosen)category=inferredAssetCategory(symbol);refreshCategory();showSuggestions();};
    symbolInput.onfocus=showSuggestions;symbolInput.onblur=event=>{if(!suggestions.contains(event.relatedTarget)&&!categories.contains(event.relatedTarget))closeSuggestions();};
    symbolInput.onkeydown=event=>{
     if(event.key==='Escape'&&!suggestions.hidden){event.preventDefault();event.stopPropagation();closeSuggestions();return;}
     if(event.key==='ArrowDown'||event.key==='ArrowUp'){
      event.preventDefault();if(suggestions.hidden)showSuggestions();const enabled=matches.map((a,i)=>a.alreadyAdded?-1:i).filter(i=>i>=0);if(!enabled.length)return;
      const position=enabled.indexOf(activeIndex),next=event.key==='ArrowDown'?(position+1)%enabled.length:(position<0?enabled.length-1:(position-1+enabled.length)%enabled.length);activeIndex=enabled[next];
      [...suggestions.children].forEach((option,index)=>option.setAttribute('aria-selected',String(index===activeIndex)));symbolInput.setAttribute('aria-activedescendant','asset-suggestion-'+activeIndex);suggestions.children[activeIndex]?.scrollIntoView?.({block:'nearest'});return;
     }
     if(event.key==='Enter'&&!suggestions.hidden&&activeIndex>=0){event.preventDefault();selectSuggestion(matches[activeIndex]);}
    };refreshCategory();

   }else{
    const title=node('div');title.className='library-instrument';title.append(node('h3',draft.symbol||draft.id),assetBadge(marketCategory(draft)));form.append(title);
    form.append(node('p','Choose when to scan and what to look for.'));
    const connection=node('div');connection.className='library-connection';connection.append(node('span','Data source'),node('strong',assetProviderNames[draft.provider]||draft.provider),node('small',existing&&!converted?assetHealth(draft,settings).detail:'Selected automatically. Candle access is checked by the scanner after saving.'));form.append(connection);
    if(draft.provider==='alpaca')form.append(node('small','US-listed stocks and ETFs · candles delayed by 16 minutes.'));
    if(draft.provider==='kraken')form.append(node('small','Monthly candles are unavailable on this connection.'));
    if(existing&&(draft.provider==='csv'||!draft.feed_confirmed))form.append(button('Connect automatic data',()=>{step=1;symbol=existing.symbol||existing.id;category=inferredAssetCategory(symbol);render();}));
    enabled=node('input');enabled.type='checkbox';enabled.checked=draft.enabled!==false;enabled.setAttribute('aria-label','Scan this asset');const toggle=node('label');toggle.className='library-toggle';toggle.append(enabled,node('strong','Scan this asset'),node('small','Turn off to pause without removing it.'));form.append(toggle);controls.push(enabled);
    const frameControls=assetTimeframeControls(draft),strategyControls=assetStrategyControls(draft);selected=frameControls.inputs;strategyInputs=strategyControls.inputs;controls.push(...Object.values(selected),...Object.values(strategyInputs));form.append(frameControls.field,strategyControls.field);
    if(existing&&!converted)form.append(assetConnectionDetails(existing,settings));
    if(converted)form.append(node('p','This replaces the current connection with an automatic data source.'));
   }
   const actions=node('div');actions.className='library-form-actions';
   const back=button(step===2&&!existing&&!addedDraft?'Back':'Cancel',()=>{if(step===2&&!existing&&!addedDraft){draft={...draft,enabled:enabled.checked,timeframes:assetFrames.filter(f=>selected[f].checked),scan_strategies:Object.keys(strategyInputs).filter(s=>strategyInputs[s].checked)};step=1;render();}else dialog.close();});
   const submit=node('button',step===1?'Continue':existing?'Save changes':'Add asset');submit.type='submit';submit.className='library-primary';actions.append(back,submit);form.append(error,actions);controls.push(back,submit);
   if(existing&&step===2){
    const removeBox=node('div');removeBox.className='library-remove';const remove=button('Remove asset',()=>{removeBox.replaceChildren(node('p',`Remove ${existing.id} from your assets? Stored signal history will remain.`),button('Keep asset',render),button('Yes, remove',async()=>{if(busy)return;busy=true;removeBox.replaceChildren(node('p','Removing…'));try{await request('/api/assets',{action:'remove',revision:settings.revision,ticker:existing.id});if(!active())return;dialog.close();await loadAssets();message(existing.id+' removed.');}catch(e){if(active()){busy=false;removeBox.replaceChildren(node('p',e.message),button('Back to settings',render));}}finally{busy=false;}}));});removeBox.append(remove);form.append(removeBox);controls.push(remove);
   }
   panel.replaceChildren(form);symbolInput?.focus?.();
   form.onsubmit=async event=>{
    event.preventDefault();if(busy)return;error.textContent='';
    if(step===1){
     try{
      symbol=symbolInput.value.trim().toUpperCase();instrument=other.value;
      const candidate=automaticAsset(symbol,category,instrument);
      const key=v=>String(v||'').toUpperCase().replaceAll('/','');
      const duplicate=settings.config.assets.find(a=>a.id!==existingId&&(key(a.id)===key(candidate.id)||(a.symbol&&key(a.symbol)===key(candidate.symbol))));
      if(duplicate)throw Error('Already in your assets: '+duplicate.id+'. Edit it from the library.');
      const same=draft&&draft.symbol===candidate.symbol&&draft.provider===candidate.provider;
      if(existing){candidate.id=existing.id;candidate.enabled=draft.enabled;candidate.scan_strategies=draft.scan_strategies;candidate.timeframes=draft.timeframes.filter(f=>assetFrameAvailable(candidate,f));converted=true;}
      else if(same){candidate.enabled=draft.enabled;candidate.timeframes=draft.timeframes;candidate.scan_strategies=draft.scan_strategies;}
      draft=candidate;step=2;render();
     }catch(e){error.textContent=e.message;}return;
    }
    const timeframes=assetFrames.filter(f=>selected[f].checked),scan_strategies=Object.keys(strategyInputs).filter(s=>strategyInputs[s].checked);
    if(!timeframes.length||!scan_strategies.length){error.textContent='Choose at least one timeframe and one strategy.';return;}
    if(enabled.checked&&draft.provider!=='csv'&&!draft.feed_confirmed){error.textContent='This connection is not configured. Check its data connection details.';return;}
    const asset={...draft,enabled:enabled.checked,timeframes,scan_strategies};busy=true;const disabled=controls.map(c=>c.disabled);controls.forEach(c=>c.disabled=true);submit.textContent='Saving…';
    try{
     if(!existing&&!addedDraft){settings=await request('/api/assets',{action:'bulk',revision:settings.revision,tickers:asset.id});addedDraft=true;}
     await request('/api/assets',{action:'save',revision:settings.revision,asset});
     if(!active())return;if(!existing){assetLibrary.category='all';assetLibrary.query='';assetLibrary.view='portfolio';}dialog.close();await loadAssets();message(asset.id+' saved. '+(asset.enabled?'Data checks will follow the scanner schedule.':'Scanning is paused.'));
    }catch(e){if(active()){error.textContent=e.message+(addedDraft?' A paused draft remains in your assets. Retry saving or reopen it to finish.':' Your edits are still here. Reopen the asset if its settings changed elsewhere.');if(addedDraft)back.textContent='Cancel';}}
    finally{busy=false;controls.forEach((c,i)=>c.disabled=disabled[i]);submit.textContent=existing?'Save changes':'Add asset';}
   };
  }
  render();
 }catch(e){if(active())panel.replaceChildren(node('p',e.message),button('Retry',()=>openAssetSetup(ticker,existingId)));}
}
async function loadAssets(){
 const generation=++assetPageGeneration;content.replaceChildren(node('p','Loading your assets…'));
 try{const settings=await request('/api/assets');if(generation!==assetPageGeneration)return;state=settings;renderAssetLibrary(settings);}
 catch(e){if(generation===assetPageGeneration)content.replaceChildren(node('h2','Assets'),node('p','Your assets could not be loaded. '+e.message),button('Try again',loadAssets));}
}
function renderAssetLibrary(settings){
 const assets=settings.config.assets,page=node('section');page.className='asset-library';
 const header=node('div');header.className='library-heading';const intro=node('div');intro.append(node('span','YOUR MARKET WATCHLIST'),node('h2','Assets'),node('p','One place for everything you follow.'));
 const add=button('+ Add asset',()=>openAssetSetup());add.className='library-primary';header.append(intro);page.append(header);const addSection=node('section');addSection.className='library-add-section';const addCopy=node('div');addCopy.append(node('h3','Add a new asset'),node('p','Find a symbol in the cached list and set up scanning.'));addSection.append(addCopy,add);page.append(addSection,node('h3','Manage existing assets'));
 const summary=node('div');summary.className='library-summary';
 for(const [value,label] of [[assets.length,'assets'],[assets.filter(a=>a.enabled!==false).length,'scan enabled'],[assets.filter(a=>['attention'].includes(assetHealth(a,settings).tone)).length,'need attention']]){const stat=node('div');stat.append(node('strong',String(value)),node('span',label));summary.append(stat);}page.append(summary);
 const views=node('div');views.className='library-views';views.setAttribute('role','group');views.setAttribute('aria-label','Asset collections');const body=node('div');body.className='library-body';let viewGeneration=0;
 const options=[['portfolio','My assets'],['forex','Shared forex'],...(settings.config.weekly_stock_screen?.enabled?[['stocks','Stock screen']]:[])];if(!options.some(([key])=>key===assetLibrary.view))assetLibrary.view='portfolio';
 const viewButtons=[];for(const [key,title] of options){const choice=button(title,()=>{assetLibrary.view=key;renderView();});choice.dataset.view=key;views.append(choice);viewButtons.push(choice);}page.append(views,body);
 function renderView(){
  const current=++viewGeneration;for(const choice of viewButtons)choice.setAttribute('aria-pressed',String(choice.dataset.view===assetLibrary.view));body.replaceChildren();
  if(assetLibrary.view==='forex'){const panel=node('div');panel.className='library-service';body.append(panel);loadColmexRequests(panel);return;}
  if(assetLibrary.view==='stocks'){
   const panel=node('div');panel.className='library-service';const stocks=settings.weekly_stock_selection;panel.append(node('h3','Automatic stock selection'),node('p','S&P 400/500 stocks touching the weekly Bollinger Bands, selected at 08:00 Israel time. These selections are separate from your saved assets.'));
   if(stocks){panel.append(node('p',`${stocks.day} · ${stocks.status}${stocks.reason?' · '+stocks.reason:''}`));for(const side of ['upper','lower'])panel.append(selectionTable([side==='upper'?'Upper band':'Lower band','Ticker','Distance (%)'],(stocks.selected?.[side]||[]).map(r=>[side,r.symbol,r.distance_pct.toFixed(3)]),(stocks.selected?.[side]||[]).map(r=>'STOCK-'+r.symbol)));}else panel.append(node('p','Waiting for the next stock screen.'));body.append(panel);return;
  }
  const toolbar=node('div');toolbar.className='library-toolbar';const search=node('input');search.type='search';search.placeholder='Search symbol or name';search.value=assetLibrary.query;search.setAttribute('aria-label','Search assets');
  const filters=node('div');filters.className='library-filters';filters.setAttribute('role','group');filters.setAttribute('aria-label','Filter asset category');const filterButtons=[];
  for(const [key,title] of [['all','All'],['stock','Stocks'],['crypto','Crypto'],['forex','Forex'],['other','Other']]){const choice=button(title,()=>{assetLibrary.category=key;renderCards();});choice.dataset.category=key;filterButtons.push(choice);filters.append(choice);}
  toolbar.append(search,filters);const count=node('p');count.className='library-count';count.setAttribute('aria-live','polite');const grid=node('div');grid.className='library-grid';body.append(toolbar,count,grid);
  search.oninput=()=>{assetLibrary.query=search.value;renderCards();};
  function renderCards(){
   if(current!==viewGeneration)return;
   for(const choice of filterButtons)choice.setAttribute('aria-pressed',String(choice.dataset.category===assetLibrary.category));
   const query=assetLibrary.query.trim().toLowerCase();const visible=assets.filter(a=>(assetLibrary.category==='all'||assetGroup(a)===assetLibrary.category)&&[a.id,a.symbol,a.name].some(v=>String(v||'').toLowerCase().includes(query)));
   count.textContent=`${visible.length} of ${assets.length} assets`;grid.replaceChildren();
   if(!visible.length){const empty=node('div');empty.className='library-empty';empty.append(node('span',assets.length?'⌕':'+'),node('h3',assets.length?'No matching assets':'Start with your first asset'),node('p',assets.length?'Try another symbol or clear your filters.':'Add a stock, crypto pair or currency pair. We’ll take care of the data source.'),button(assets.length?'Clear filters':'Add an asset',()=>{if(assets.length){assetLibrary.query='';assetLibrary.category='all';search.value='';renderCards();}else return openAssetSetup();}));grid.append(empty);return;}
   for(const asset of visible){
    const card=node('article');card.className='library-card';card.setAttribute('aria-label',asset.id);const health=assetHealth(asset,settings),top=node('div');top.className='library-card-top';top.append(node('span',marketCategory(asset)),assetBadge(health.label,health.tone));
    const title=node('h3',asset.symbol||asset.id),name=node('p',asset.name||(asset.symbol&&asset.symbol!==asset.id?asset.id:marketCategory(asset)));name.className='library-name';card.append(top,title,name);
    const chips=node('div');chips.className='library-frame-chips';for(const frame of asset.timeframes||defaultAssetFrames)chips.append(node('span',assetFrameNames[frame]||frame));card.append(chips);
    const strategies=node('p',(asset.scan_strategies||['rsi','pin-bars','rsi-pb']).map(s=>assetStrategyNames[s]||s).join(' · '));strategies.className='library-strategies';card.append(strategies);
    const footer=node('div');footer.className='library-card-footer';footer.append(node('small',health.detail));const edit=button(health.label==='Needs setup'?'Set up asset':'Edit asset',()=>openAssetSetup(asset.symbol||asset.id,asset.id));edit.setAttribute('aria-label',`Edit ${asset.id}`);footer.append(edit);card.append(footer);grid.append(card);
   }
  }
  renderCards();
 }
 renderView();
 if(settings.sync?.error||settings.configuration_conflict||settings.conflicts){const note=node('aside');note.className='library-notice';note.append(node('p',settings.sync?.error?'Configuration sync needs attention: '+settings.sync.error:'There are configuration changes to reconcile.'));
 if(!online&&settings.configuration_conflict)note.append(button('Use online configuration',()=>mutate({action:'accept_online'})));page.append(note);}
 content.replaceChildren(page);
}

async function mutate(body){try{message('Saving…');const result=await request('/api/assets',{...body,revision:state.revision});await loadAssets();message(result.results.map(r=>r.ticker+': '+r.status).join('; ')+(online?' — pending local scanner':' — saved locally'));}catch(e){message(e.message,true);}}
function displayTime(ms){return new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Jerusalem',dateStyle:'medium',timeStyle:'short'}).format(new Date(ms));}
function table(headers,rows){const t=node('table'),head=node('tr');for(const h of headers){const th=node('th');th.append(h instanceof Node?h:node('span',h));if(h instanceof Node&&h.dataset.sortState)th.setAttribute('aria-sort',h.dataset.sortState);head.append(th);}t.append(head);for(const row of rows){const tr=node('tr');for(const value of row){const td=node('td');td.append(value instanceof Node?value:node('span',String(value??'')));tr.append(td);}t.append(tr);}const wrap=node('div');wrap.className='scroll';wrap.append(t);return wrap;}
function selectionTable(headers,rows,ids){const grid=table(headers,rows);for(const [index,tr] of [...grid.querySelectorAll('tr')].slice(1).entries()){const coverage=(state.coverage||[]).filter(r=>r.asset===ids[index]),warning=coverage.filter(r=>/unavailable|insufficient|partial/.test(r.status));if(warning.length)tr.classList.add('error');tr.classList.add('finding-row');tr.tabIndex=0;tr.setAttribute('aria-expanded','false');tr.title='Click for feed and candle coverage';let detail;const toggle=()=>{if(!detail){detail=node('tr');const td=node('td');td.colSpan=headers.length;td.textContent=coverage.length?coverage.map(r=>`${r.timeframe} · ${r.provider} · ${r.status}${r.last_closed_at?' · latest close '+displayTime(r.last_closed_at):''}${r.next_close_at?' · next check '+displayTime(r.next_close_at):''}${r.reason?' · '+r.reason:''}`).join(' | '):'Awaiting the scanner’s first coverage check for this selection.';detail.append(td);tr.after(detail);}else detail.hidden=!detail.hidden;tr.classList.toggle('selected-row',!detail.hidden);tr.setAttribute('aria-expanded',String(!detail.hidden));};tr.onclick=toggle;tr.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();toggle();}};}return grid;}
let page=0,showArchived=false;
const backtestSort={key:'confirmed_at',order:'asc'};
const strategyViews={live:'rsi',backtest:'rsi'};
function strategySelector(source){const label=node('label','Strategy '),select=node('select');select.setAttribute('aria-label','Strategy view');for(const [key,name] of [['rsi','RSI divergence'],['momentum','Daily momentum'],['pin-bars','Pin-Bars'],['bb-pin-bars','BB Pin-Bars'],['rsi-pb','RSI + PB']]){const option=node('option',name);option.value=key;select.append(option);}select.value=strategyViews[source];select.onchange=()=>{strategyViews[source]=select.value;page=0;if(source==='backtest'){csvReportId=null;csvPage=0;}findings(source).catch(e=>message(e.message,true));};label.append(select);return label;}
async function findings(source){
 if(['pin-bars','bb-pin-bars','rsi-pb'].includes(strategyViews[source]))return source==='live'?pinBarFindings(strategyViews[source]):csvBacktestFindings(strategyViews[source]);
 if(strategyViews[source]==='momentum')return momentumFindings(source);
 const result=await request(`/api/findings?source=${source}&page=${page}&archived=${showArchived}${source==='backtest'?`&sort=${backtestSort.key}&order=${backtestSort.order}`:''}`);if(strategyViews[source]!=='rsi')return;content.replaceChildren(node('h2',source==='live'?'Signal workspace':'Historical research'));
 content.append(strategySelector(source));
 if(source==='live'){const label=node('label','Show archived'),check=node('input');check.type='checkbox';check.checked=showArchived;check.onchange=()=>{showArchived=check.checked;page=0;findings(source).catch(e=>message(e.message,true));};label.prepend(check);content.append(label);}
 const metrics=node('div');metrics.className='metrics';for(const [label,value] of [['Findings on this page',result.findings.length],['Markets covered',(result.summary||[]).length],['Needs attention',(result.summary||[]).filter(r=>/unavailable|insufficient|partial/.test(r.status)).length]]){const card=node('div');card.append(node('span',label),node('strong',value));metrics.append(card);}content.append(metrics);
 if(source==='backtest'){
  const exportPath='/api/findings?source=backtest&sort='+backtestSort.key+'&order='+backtestSort.order;
  content.append(button('Configure & re-run backtests',configureBacktest),button('Upload CSV & backtest',uploadCSV),button('Uploaded CSV results',()=>csvBacktestFindings()),exportCSVButton('backtest-results',async()=>(await allResultPages(exportPath,'findings')).map(r=>({...r.signal,finding_id:r.id,followup:r.followup,feedback:r.feedback}))));
 }
 const tabs=node('div');tabs.className='mini-tabs';tabs.setAttribute('role','tablist');const panel=node('section');content.append(tabs,panel);
 function sortableHeader(label,key){
  if(source!=='backtest'||!key)return label;
  const active=backtestSort.key===key,b=button(label+(active?(backtestSort.order==='asc'?' ↑':' ↓'):' ↕'),()=>{
   backtestSort.order=active&&backtestSort.order==='asc'?'desc':'asc';backtestSort.key=key;page=0;
   findings(source).catch(e=>message(e.message,true));
  });b.className='sort-header';b.dataset.sortState=active?(backtestSort.order==='asc'?'ascending':'descending'):'none';
  b.title=key==='rating'?'Sort by your star rating (unrated last)':key==='evidence'?'Sort by direction, then stage':`Sort by ${label}`;return b;
 }
 const rows=result.findings.map(r=>{const s=r.signal;const detail=node('div');detail.append(node('strong',s.direction.toUpperCase()),node('p',s.stage_description||(s.signal_status==='provisional'?'Candle-close divergence · Telegram alert stage; subsequent candles can invalidate the pivot.':'Structurally confirmed · the following candle confirms the pivot; no additional Telegram alert.')),node('small',`${Number(s.price1.toPrecision(8))} → ${Number(s.price2.toPrecision(8))} · RSI ${s.rsi1.toFixed(2)} → ${s.rsi2.toFixed(2)}${Number.isFinite(s.band_slope_pct)?' · saved outer BB slope '+s.band_slope_pct.toFixed(2)+'%/candle':''}`));if(r.archived)detail.append(node('small','Archived'));const actions=node('div');actions.append(manageAssetButton(s),button('Inspect candles',()=>openCandle(source,r.id)),button('Follow up',()=>{selectFinding(source,r.id);openFollowup(source,r);}));if(source==='live')actions.append(button(r.archived?'Restore':'Archive',()=>changeFinding(r,r.archived?'restore':'archive')),button('Delete',()=>changeFinding(r,'delete')));return[s.symbol,s.timeframe,detail,outcomeCell(r),displayTime(s.confirmed_at),actions,feedback(source,r)];});
 function show(which){panel.replaceChildren();for(const b of tabs.children)b.setAttribute('aria-selected',String(b.textContent===which));if(which==='Finding details'){const grid=table([['Asset','asset'],['Timeframe','timeframe'],['Divergence status','evidence'],['Observed gain / drawdown',null],['Signal time · Israel','confirmed_at'],['Actions',null],['Your review','rating']].map(([label,key])=>sortableHeader(label,key)),rows);for(const [index,tr] of [...grid.querySelectorAll('tr')].slice(1).entries()){tr.classList.add('finding-row');tr.dataset.findingId=result.findings[index].id;tr.classList.toggle('selected-row',selectedFindings[source]===tr.dataset.findingId);tr.setAttribute('aria-selected',String(selectedFindings[source]===tr.dataset.findingId));tr.tabIndex=0;tr.setAttribute('aria-label',`Inspect candles for ${result.findings[index].signal.symbol}`);tr.onclick=e=>{if(!e.target.closest('button,a,input,textarea,select,label'))openCandle(source,result.findings[index].id);};tr.onkeydown=e=>{if(e.target===tr&&(e.key==='Enter'||e.key===' ')){e.preventDefault();openCandle(source,result.findings[index].id);}};}panel.append(grid);if(!rows.length)panel.append(node('p','No findings in this view yet. Check coverage for unavailable feeds.'));const pager=node('nav');pager.className='pagination';pager.setAttribute('aria-label','Backtest pages');
  const go=(target)=>{page=target;findings(source).catch(e=>message(e.message,true));};
  const pages=Math.max(result.page_count??(page+Number(result.has_more)),1);const addPage=(label,target,current=false,disabled=false)=>{const b=button(label,()=>go(target));b.disabled=disabled;if(current)b.setAttribute('aria-current','page');pager.append(b);};
  if(source==='backtest'){
   addPage('First',0,false,page===0);addPage('Previous',Math.max(0,page-1),false,page===0);
   let visible=pages<=7?Array.from({length:pages},(_,i)=>i):[...new Set([0,Math.max(0,page-1),page,Math.min(pages-1,page+1),pages-1])].sort((a,b)=>a-b),last=-1;
   for(const n of visible){if(n>last+1)pager.append(node('span','…'));addPage(String(n+1),n,n===page);last=n;}
   addPage('Next',Math.min(pages-1,page+1),false,page>=pages-1);addPage('Last',Math.max(0,pages-1),false,page>=pages-1);
   pager.append(node('small',`${result.total_count??0} findings · page ${page+1} of ${pages}`));
  }else{
   addPage('Previous',Math.max(0,page-1),false,page===0);pager.append(node('span',` Page ${page+1} `));addPage('Next',page+1,false,!result.has_more);
  }
  panel.append(pager);}else panel.append(table(['Asset','Timeframe','Status','Matches','Coverage details'],(result.summary||[]).map(r=>[r.asset,r.timeframe,r.status,r.signals??r.historical_matches??0,r.note||r.reason||r.data_notes||''])));}
 for(const label of ['Finding details','Coverage summary']){const b=button(label,()=>show(label));b.setAttribute('role','tab');tabs.append(b);}show('Finding details');
}
async function changeFinding(r,action){
 if(action==='delete'&&!confirm('Delete this signal from the workspace? It cannot be restored.'))return;
 try{await request('/api/finding-state',{source:'live',finding_id:r.id,action,revision:r.state_revision||0});await findings('live');message(action==='archive'?'Signal archived.':action==='restore'?'Signal restored.':'Signal deleted.');}catch(e){message(e.message,true);}
}
async function configureBacktest(){try{const options=await request('/api/backtests');const dialog=$('backtest-dialog'),form=$('backtest-form');form.replaceChildren(node('p',`Replay ${options.year} history with the selected data source. Missing combinations are reported as unavailable.`));
 const checks=(title,values)=>{const field=node('fieldset');field.append(node('legend',title));const selected=[];for(const value of values){const l=node('label',value),i=node('input');i.type='checkbox';i.value=value;i.checked=title!=='Timeframes'||value!=='1h';selected.push(i);l.prepend(i);field.append(l);}form.append(field);return()=>selected.filter(i=>i.checked).map(i=>i.value);};
 const assets=checks('Historical asset names',options.assets),timeframes=checks('Timeframes',options.timeframes);const strategy=node('select');strategy.setAttribute('aria-label','Strategy');for(const [key,text] of Object.entries(options.strategies)){const o=node('option',key==='confirmed'?'Confirmed RSI divergence':'Warm-up divergence');o.value=key;strategy.append(o);}const logic=node('p');logic.className='strategy-logic';strategy.onchange=()=>logic.textContent=options.strategies[strategy.value];strategy.onchange();form.append(node('label','Strategy'),strategy,logic);const source=node('select');source.setAttribute('aria-label','Historical data source');for(const key of options.sources||['csv']){const o=node('option',key==='alpaca'?'Alpaca US equities':key==='tiingo'?'Tiingo forex':key==='fmp'?'FMP cash indices':'Stored CSV history');o.value=key;source.append(o);}form.append(node('label','Data source'),source);source.onchange=()=>{const allowed=source.value==='alpaca'?options.alpaca_assets:source.value==='tiingo'?options.tiingo_assets:source.value==='fmp'?options.fmp_assets:(options.csv_assets||options.assets);for(const i of form.querySelectorAll('fieldset:first-of-type input')){i.disabled=!allowed.includes(i.value);i.checked=!i.disabled;}};source.onchange();if(options.sources?.includes('alpaca'))form.append(node('p','Alpaca supports US equities; Tiingo supports forex; FMP supports daily, weekly and monthly cash indices. Insufficient warmup is reported as partial history.'));const run=node('button','Run backtest');run.type='submit';form.append(run);form.onsubmit=async e=>{e.preventDefault();run.disabled=true;try{const job=await request('/api/backtests',{assets:assets(),timeframes:timeframes(),strategy:strategy.value,source:source.value});dialog.close();message('Backtest running…');watchBacktest(job.id);}catch(e){message(e.message,true);run.disabled=false;}};dialog.showModal();}catch(e){message(e.message,true);}}
async function watchBacktest(id){try{const value=await request('/api/backtests?job='+encodeURIComponent(id));if(value.job?.id===id&&['completed','failed'].includes(value.job.status)){message(value.job.error||'Backtest completed',value.job.status==='failed');page=0;await findings('backtest');}else setTimeout(()=>watchBacktest(id),2000);}catch(e){message(e.message,true);}}
async function loadLogs(){content.replaceChildren(node('h2','Activity & diagnostics'));const filter=node('select');for(const [value,label] of [['all','All events'],['errors','Errors / scans needing attention']]){const o=node('option',label);o.value=value;filter.append(o);}const search=node('input');search.placeholder='Search asset, event or failure reason';search.setAttribute('aria-label','Search logs');const order=node('select');for(const [value,label] of [['newest','Newest first'],['oldest','Oldest first'],['errors','Errors first']]){const o=node('option',label);o.value=value;order.append(o);}const panel=node('div');const refresh=button('Refresh logs',async()=>{refresh.disabled=true;try{rows=(await request('/api/logs?filter='+filter.value)).rows;render();message('Logs refreshed.');}catch(e){message(e.message,true);}finally{refresh.disabled=false;}});content.append(filter,search,order,refresh,panel);let rows=[];
 const failed=r=>/error|failed|unavailable|insufficient|retry/i.test(r.event+' '+r.details);
 function render(){const visible=rows.filter(r=>(filter.value==='all'||failed(r))&&(r.event+' '+r.details).toLowerCase().includes(search.value.toLowerCase())).sort((a,b)=>order.value==='errors'?(Number(failed(b))-Number(failed(a))||b.occurred-a.occurred):order.value==='oldest'?a.occurred-b.occurred:b.occurred-a.occurred);panel.replaceChildren(table(['Time · Israel','Event','Details','System data'],visible.map(r=>{const time=node('div');time.append(node('strong',displayTime(r.occurred*1000)),node('small',`${Math.max(0,Math.floor((Date.now()/1000-r.occurred)/60))} min ago`));let details=r.details;try{const d=JSON.parse(details);details=`${d.asset||''} · ${d.timeframe||''} · ${d.status||''}. ${d.reason||d.data_notes||''} ${d.candles!==undefined?d.candles+' candles evaluated.':''}`;}catch{}const system=node('small',`Event reference: ${r.occurred}`);system.title='Unix timestamp';return[time,r.event,details,system];})));}
 rows=(await request('/api/logs')).rows;filter.onchange=async()=>{try{rows=(await request('/api/logs?filter='+filter.value)).rows;render();}catch(e){message(e.message,true);}};order.onchange=render;search.oninput=render;render();}

function feedback(source,r){const wrap=node('div');wrap.className='feedback';let rating=r.feedback?.rating??null,revision=r.feedback?.revision||0,timer,saving=false,dirty=false;const stars=[],reviews=node('div');reviews.className='review-ratings';const userStars=node('div');userStars.className='user-stars';userStars.setAttribute('aria-label','Your quality rating');const comment=node('textarea');comment.maxLength=4000;comment.value=r.feedback?.comment||'';comment.placeholder='Comment';const progress=node('span');
 for(let i=1;i<=5;i++){const b=button(i<=rating?'★':'☆',()=>{rating=i;stars.forEach((s,k)=>s.textContent=k<i?'★':'☆');changed(0);});b.className='star';b.setAttribute('aria-label',`${i} stars`);stars.push(b);userStars.append(b);}const auto=node('span');auto.className='automatic-rating';r.ratingElement=auto;updateAutomaticRating(r);reviews.append(userStars,auto);wrap.append(reviews,comment,progress);
 function changed(delay){dirty=true;clearTimeout(timer);timer=setTimeout(save,delay);progress.textContent='Saving…';}
 async function save(){if(saving)return;saving=true;const submitted={rating,comment:comment.value};dirty=false;try{const value=await request('/api/feedback',{source,finding_id:r.id,revision,...submitted});revision=value.revision;progress.textContent=online?'Saved online':'Saved locally';}catch(e){dirty=true;progress.textContent=e.message;}finally{saving=false;if(dirty&&progress.textContent==='Saving…')timer=setTimeout(save,0);}}
 comment.oninput=()=>changed(600);comment.onblur=()=>{if(dirty&&!saving)save();};return wrap;
}
async function openCandle(source,id,target='pivot2'){selectFinding(source,id);const dialog=$('candle-dialog');if(!dialog.open)dialog.showModal();$('candle-title').textContent='Signal candles';$('candle-note').textContent='Loading candles…';$('candle-legend').textContent='';$('candle-actions').replaceChildren();$('candle-table').replaceChildren();if(chart){chart.remove();chart=null;}$('chart').replaceChildren();try{const data=await request('/api/candles?'+new URLSearchParams({source,finding:id,target}));$('candle-title').textContent=`${data.signal.symbol} · ${({monthly:'Monthly',weekly:'Weekly',daily:'Daily','4h':'4 hours','1h':'Hourly','30m':'30 minutes','15m':'15 minutes','5m':'5 minutes','60':'Hourly','240':'4 hours','D':'Daily','W':'Weekly','M':'Monthly'})[data.signal.timeframe]||data.signal.timeframe} candles${data.signal.direction?' · '+data.signal.direction.toUpperCase():''}`;const rules=data.signal.rules;$('candle-legend').textContent=`Bollinger Bands (${rules.bb_period}, ${rules.bb_multiplier}σ) · RSI (${rules.rsi_period}, Wilder).`; const slope=data.signal.band_slope_pct;if(Number.isFinite(slope)){const angle=Math.atan(slope/100)*180/Math.PI;$('candle-legend').textContent+=` ${data.signal.direction==='bullish'?'Lower':'Upper'} touching BB slope: ${slope>=0?'+':''}${slope.toFixed(3)}%/candle · normalized angle ${angle>=0?'+':''}${angle.toFixed(3)}° (${rules.band_slope_period||3} candles).`;}else $('candle-legend').textContent+=' Band slope is not used as a filter.'; $('candle-legend').title='Normalized angle = atan(slope % / 100), with one candle horizontally and a 100% price change vertically; independent of chart zoom. Indicators are recalculated from the displayed candles and may differ slightly from the values saved with the signal.'; $('candle-note').textContent='';if(online&&!data.tradingview_url){const settings=await request('/api/assets');const a=settings.config.assets.map(a=>{const p=a.timeframe_providers?.[data.signal.timeframe]||a.provider;return p==='kraken'?{...a,provider:p,exchange:'Kraken',tradingview_symbol:a.provider==='kraken'?a.tradingview_symbol:''}:p==='oanda'?{...a,provider:p,exchange:data.signal.symbol.split(':')[1],tradingview_symbol:a.provider==='oanda'?a.tradingview_symbol:''}:p==='tiingo'?{...a,provider:p,exchange:'Tiingo-UTC',tradingview_symbol:a.provider==='tiingo'?a.tradingview_symbol:''}:p==='alpaca'?{...a,provider:p,exchange:data.signal.symbol.split(':')[1],tradingview_symbol:a.provider==='alpaca'?a.tradingview_symbol:''}:a;}).find(a=>a.id===data.signal.symbol||`${a.provider}:${a.exchange||'configured'}:${a.symbol||a.id}`===data.signal.symbol);if(a?.tradingview_symbol){const interval={monthly:'M',weekly:'W',daily:'D','4h':'240','1h':'60','30m':'30','15m':'15','5m':'5'}[data.signal.timeframe];data.tradingview_url='https://www.tradingview.com/chart/?'+new URLSearchParams({symbol:a.tradingview_symbol,interval});}}const actions=$('candle-actions');actions.replaceChildren();for(const [key,label] of [['pivot1','First pivot'],['pivot2','Second pivot'],['confirmation',data.signal.signal_status==='provisional'?'Signal candle close':'Confirmation']])actions.append(button(label,()=>openCandle(source,id,key)));
 if(data.tradingview_url){const a=node('a','Open TradingView');a.href=data.tradingview_url;a.target='_blank';a.rel='noopener';a.title='Opens the configured comparison feed in TradingView.';actions.append(a);}
 const candles=data.candles;if(chart){chart.remove();chart=null;}$('chart').replaceChildren();$('candle-table').replaceChildren();
 if(candles.length){const library=await import('/web/chart.js');chart=await library.draw($('chart'),candles,data.signal,target);$('candle-table').replaceChildren(table(['Candle (Israel)','Open','High','Low','Close'],candles.filter(c=>c.start===data.signal.pivot1||c.start===data.signal.pivot2||c.end===data.signal.confirmed_at).map(c=>[displayTime(c.start),c.open,c.high,c.low,c.close])));}else{$('candle-note').textContent+=' No stored source candles are available for this finding.';}
 }catch(e){$('candle-note').textContent=e.name==='AbortError'?'Loading candles timed out. Please try again.':'Could not load candles: '+e.message;message($('candle-note').textContent,true);}}
$('close-backtest').onclick=()=>$('backtest-dialog').close();
$('close-chart').onclick=()=>$('candle-dialog').close();
$('candle-dialog').addEventListener('close',()=>{if(chart){chart.remove();chart=null;}if(location.pathname==='/candle'){const source=new URLSearchParams(location.search).get('source');location.assign(source==='backtest'?'/backtest':'/signals');}});
async function load(){if(online&&passwordSetupSession)return;if(online&&!token){$('login').hidden=false;return;}try{const path=location.pathname;$('connection').textContent=online?'Online database':'Local SQLite';if(path==='/assets')await loadAssets();else if(path==='/candle'){const p=new URLSearchParams(location.search);await openCandle(p.get('source'),p.get('finding'));}else if(path==='/backtest')await findings('backtest');else if(path==='/logs')await loadLogs();else if(path==='/signals'||path==='/dashboard')await findings('live');else await loadOverview();}catch(e){message(e.message,true);}}
load();

for(const a of document.querySelectorAll?.('nav a')||[])if(a.getAttribute('href')===location.pathname)a.setAttribute('aria-current','page');

async function loadOverview(){
 const data=await request('/api/findings?source=live&page=0');
 const monitor=node('section');monitor.className='overview-monitor';monitor.setAttribute('aria-labelledby','market-monitor-title');
 const title=node('h2','Market monitor');title.id='market-monitor-title';
 const description=node('p','Closed-candle divergence research · live scanner and alert delivery');description.className='overview-description';
 const actions=node('div');actions.className='overview-actions';
 const review=button('Review signals',()=>location.assign('/signals'));review.className='overview-primary';
 actions.append(review,button('Configure backtest',configureBacktest),button('Catch up',requestCatchup));
 const resources=node('div');resources.className='overview-resources';
 const whitepaper=node('a','Strategy white paper');whitepaper.href='https://docs.google.com/document/d/1UbzPu5QlFio5KPctVnYKV-VTwCsi2Q4kXsCicfLCTCA/edit?usp=sharing';whitepaper.target='_blank';whitepaper.rel='noopener';
 const download=node('a','Download Windows app');download.href='https://github.com/idodidodi/trading-bot/releases/latest/download/FamilyTradingBot.exe';
 resources.append(whitepaper,download);monitor.append(title,description,actions,resources);content.replaceChildren(monitor);
 if(!online){const updates=node('div');updates.className='overview-updates';monitor.append(updates);loadUpdateControls(updates);}
 const scan=data.scan;if(scan){content.append(node('p',`Last cycle: ${displayTime(scan.scanned_at)} · ${scan.phase||'scanned'}. ${scan.next_scan_at?'Next scan: '+displayTime(scan.next_scan_at):'Awaiting scanner schedule.'}`));}else content.append(node('p','Online coverage reflects the latest successful local sync.'));const coverage=data.summary||[];const failures=coverage.filter(r=>/unavailable|insufficient/.test(r.status));if(failures.length){const warning=node('p',`${failures.length} asset/timeframe combinations could not be scanned. See coverage below and Logs for reasons.`);warning.className='error';content.append(warning);}content.append(table(['Asset','Timeframe','Coverage','Latest closed candle · Israel','Details'],coverage.map(r=>[r.asset,r.timeframe,r.status,r.last_closed_at?displayTime(r.last_closed_at):'Unavailable',r.reason||r.data_notes||(r.limited_history?'Limited history':'')])));}


function updateAutomaticRating(r){if(!r.ratingElement)return;const f=r.followup;r.ratingElement.textContent=f?.rating?`Auto: ${f.rating} / 5${f.mode==='all'?' · all history':''}${f.status==='ongoing'?' · ongoing':''}`:'Auto: awaiting follow-up';r.ratingElement.title=`Based on maximum directional gain divided by maximum drawdown. Window: ${f?.mode==='all'?'all available history':'through first recovery'}.`;}
const elapsedMinutes=ms=>{if(ms==null)return '—';const minutes=Math.round(ms/60000);return `${Math.floor(minutes/60)}:${String(minutes%60).padStart(2,'0')}`;};
async function openFollowup(source,r,mode='recovery'){
 const dialog=$('followup-dialog'),panel=$('followup-content');if(!dialog.open)dialog.showModal();$('followup-title').textContent=`${r.signal.symbol} · ${r.signal.timeframe} follow-up`;panel.replaceChildren(node('p','Calculating follow-up…'));
 const requestId=(dialog.dataset.requestId=String(Number(dialog.dataset.requestId||0)+1));
 try{const data=await request(`/api/followup?source=${source}&finding=${r.id}&mode=${mode}`);if(dialog.dataset.requestId!==requestId)return;const f=data.followup;r.followup=f;updateAutomaticRating(r);updateOutcome(r);panel.replaceChildren();
 const select=node('select');select.setAttribute('aria-label','Follow-up window');for(const [value,label] of [['recovery','Through first recovery'],['all','All available history']]){const o=node('option',label);o.value=value;select.append(o);}select.value=mode;select.onchange=()=>openFollowup(source,r,select.value);panel.append(select);
 panel.append(node('p',`From second divergence pivot: ${displayTime(f.baseline_at)} · price ${Number(f.baseline_price.toPrecision(8))}. ${r.signal.direction==='bullish'?'Bullish: upward moves count as gain.':'Bearish: downward moves count as gain.'}`));
 if(f.status==='unavailable'){panel.append(node('p',f.reason));return;}
 panel.append(table(['Outcome','Maximum move','Price level','Time from divergence · hours:minutes'],[
 ['Drawdown',`${f.drawdown_pct.toFixed(2)}%`,f.drawdown_price==null?'—':Number(f.drawdown_price.toPrecision(8)),elapsedMinutes(f.drawdown_elapsed_ms)],
 ['Gain',`${f.gain_pct.toFixed(2)}%`,f.gain_price==null?'—':Number(f.gain_price.toPrecision(8)),elapsedMinutes(f.gain_elapsed_ms)],
 [mode==='all'?'Recovery after worst drawdown':'First recovery',f.recovery_at?'Returned to baseline':f.status==='no drawdown'?'No adverse move':'Not recovered yet',f.recovery_at?displayTime(f.recovery_at):'—',elapsedMinutes(f.recovery_elapsed_ms)]
 ]));
 panel.append(node('p',`Automatic rating: ${f.rating} / 5 · Gain / drawdown: ${f.gain_drawdown_ratio==null?(f.gain_pct?'No drawdown':'—'):f.gain_drawdown_ratio.toFixed(2)} · ${f.candles} closed candles through ${displayTime(f.through_at)}.`));
 panel.append(node('p','Rating: 5 for gain/drawdown ≥ 3 (or gain with no drawdown), 4 for ≥ 2, 3 for ≥ 1, 2 for ≥ 0.5, otherwise 1. Zero gain scores 1. The rating reflects observed history and may change as more candles close.'));
 panel.append(node('small','Elapsed times use hours:minutes, rounded to whole minutes from the pivot candle start to the close of the candle containing the extreme. The pivot candle is excluded because its wick order is unknown. Recovery requires a later candle closing at or beyond the baseline after an adverse wick. The full recovery candle is included; intrabar ordering is unknown. Missing stored candles can limit these results.'));
 }catch(e){if(dialog.dataset.requestId===requestId)panel.replaceChildren(node('p',e.message));}
}
$('close-followup').onclick=()=>$('followup-dialog').close();

async function requestCatchup(){try{const result=await request('/api/catchup',{action:'request'});message(result.status==='pending'?'Catch-up queued. The scanner will process missed closed candles and send a Telegram summary when online.':`Catch-up ${result.status}${result.reason?': '+result.reason:''}`);watchCatchup(result.id);}catch(e){message(e.message,true);}}
async function loadUpdateControls(actions){try{const value=await request('/api/updates');if(!value.supported)return;const label=node('span',`${value.platform||'Windows'} app ${value.version} · automatic updates ${value.schedule||'enabled'}`);actions.append(label,button('Check for updates',async()=>{try{const result=await request('/api/updates',{action:'check'});message(result.message||'Checking for updates…');}catch(e){message(e.message,true);}}));}catch{}}

function connectionTestControls(panel){
 if(online)return;
 const section=node('section'),result=node('p'),actions=node('div'),buttons=[];
 section.append(node('h3','Connection tests'),node('p','Send a Telegram test message or check the Postgres connection without writing data.'));
 result.setAttribute('role','status');result.setAttribute('aria-live','polite');
 for(const [action,label] of [['telegram','Test Telegram'],['database','Test database connection']]){
  const control=button(label,async()=>{
   for(const item of buttons)item.disabled=true;
   result.className='';result.textContent=action==='telegram'?'Sending test message…':'Syncing demo signal…';
   try{const value=await request('/api/connection-test',{action},30000);result.className='success';result.textContent=value.message;}
   catch(e){result.className='error';result.textContent=e.name==='AbortError'?'Test timed out. Check Telegram or the online dashboard before retrying.':e.message;}
   finally{for(const item of buttons)item.disabled=false;}
  });
  buttons.push(control);actions.append(control);
 }
 section.append(actions,result);panel.append(section);
}
async function developmentSettingsControls(panel,isCurrent){
 if(online)return;
 const section=node('section'),result=node('p');section.className='development-settings';
 try{
  const value=await request('/api/local-settings');if(!isCurrent())return;
  const paper=node('section'),paperResult=node('p'),paperRow=node('label'),paperInput=node('input');paper.className='development-settings';paperInput.type='checkbox';paperInput.checked=value.paper_trading_enabled;paperInput.disabled=!value.paper_trading_editable;
  paperRow.append(paperInput,document.createTextNode(' Enable Alpaca paper trading'));
  paper.append(node('h3','Paper trading'),paperRow,node('p',value.paper_trading_editable?'Saved for this installation and applied without restarting. Disabling stops local automation; existing simulated orders and positions remain at Alpaca. An in-progress cycle may finish.':'Paper trading is disabled in development. Configure this setting in the local production app.'));
  if(value.paper_trading_editable){
   const savePaper=button('Save paper trading setting',async()=>{
    savePaper.disabled=true;paperResult.textContent='Saving…';paperResult.className='';
    try{await request('/api/local-settings',{paper_trading_enabled:paperInput.checked});await openVersions();}
    catch(e){paperResult.className='error';paperResult.textContent=e.message;}
    finally{savePaper.disabled=false;}
   });
   paperResult.setAttribute('role','status');paper.append(savePaper,paperResult);
  }
  panel.append(paper);if(!value.editable)return;panel.append(section);
  section.append(node('h3','Development overrides'),node('p','Use while production is stopped. Saved for this installation and applied to new deliveries without restarting. An in-progress request may finish.'));
  const inputs={};
  for(const [key,label] of [['telegram_enabled','Send Telegram alerts from development']]){
   const row=node('label'),input=node('input');input.type='checkbox';input.checked=value[key];inputs[key]=input;row.append(input,document.createTextNode(' '+label));section.append(row);
  }
  const save=button('Save development settings',async()=>{
   save.disabled=true;result.className='';result.textContent='Saving…';
   try{await request('/api/local-settings',Object.fromEntries(Object.entries(inputs).map(([key,input])=>[key,input.checked])));await openVersions();}
   catch(e){result.className='error';result.textContent=e.message;}
   finally{save.disabled=false;}
  });
  result.setAttribute('role','status');section.append(save,result,node('p','Development keeps separate data; activity logging and paper orders remain disabled. Previously processed dry-run alerts are not resent.'));
 }catch(e){if(isCurrent())panel.append(node('p','Local settings unavailable: '+e.message));}
}
let versionRequest=0;
function versionMatch(current,latest){return !current||!latest?'Comparison unavailable':current===latest?'Matches GitHub':'Differs from GitHub';}
async function personalTelegramControls(panel,isCurrent){
 const section=node('section');section.className='development-settings telegram-alerts';
 section.append(node('h3','My Telegram alerts'));panel.append(section);
 if(!online){section.append(node('p','Sign in to the online dashboard to manage your personal Telegram alerts.'));return;}
 try{
  let value=await request('/api/telegram-alerts');if(!isCurrent())return;
  section.append(node('p','Choose which alerts you receive. Changes apply only to your personal groups. Re-enabling resumes new alerts without replaying skipped alerts.'));
  if(!value.registered)section.append(node('p','Your personal Telegram groups have not been connected yet. You can save your preferences now.'));
  const inputs={},status=node('p');status.setAttribute('role','status');
  const categories=[['general','General'],...['forex','crypto','stocks','other'].flatMap(m=>[['rsi','RSI'],['pin-bars','Pin Bars'],['rsi-pb','RSI + PB']].map(([s,label])=>[m+'.'+s,m[0].toUpperCase()+m.slice(1)+' · '+label]))];
  for(const [key,label] of categories){
   const row=node('label'),input=node('input');input.type='checkbox';input.checked=value.enabled[key]!==false;
   input.setAttribute('aria-label','Receive '+label+' alerts');inputs[key]=input;row.append(input,document.createTextNode(' '+label));section.append(row);
  }
  const save=button('Save my Telegram alerts',async()=>{
   save.disabled=true;status.textContent='Saving…';
   try{value=await request('/api/telegram-alerts',{action:'save',revision:value.revision,enabled:Object.fromEntries(Object.entries(inputs).map(([key,input])=>[key,input.checked]))});status.textContent='Saved. Your personal alert preferences are updated.';}
   catch(e){status.textContent=e.message+' Reopen Settings to reload before retrying.';}
   finally{save.disabled=false;}
  });section.append(save,status);
 }catch(e){if(isCurrent())section.append(node('p','Personal Telegram settings unavailable: '+e.message));}
}
async function telegramRoutingControls(panel,isCurrent){
 const section=node('section');section.className='development-settings telegram-routing';section.append(node('h3','Telegram groups'));panel.append(section);
 try{
  let value=await request('/api/telegram-routing');if(!isCurrent())return;
  const inputs={},status=node('p');
  section.append(node('p',online?'Save group chat IDs here. Scanners refresh automatically; blank categories use General.':'Cached cloud routing. Edit destinations in the online dashboard Settings.'));
  for(const [key,label] of [['general','General'],...['forex','crypto','stocks','other'].flatMap(m=>[['rsi','RSI'],['pin-bars','Pin Bars'],['rsi-pb','RSI + PB']].map(([s,label])=>[m+'.'+s,m[0].toUpperCase()+m.slice(1)+' · '+label]))]){
   const row=node('label',label+' '),input=node('input');input.type='text';input.value=value.routes[key]||'';input.placeholder='-100…';input.disabled=!value.editable;input.setAttribute('aria-label',label+' chat ID');inputs[key]=input;row.append(input);section.append(row);
  }
  if(value.editable){
   const save=button('Save Telegram groups',async()=>{
    const routes=Object.fromEntries(Object.entries(inputs).map(([key,input])=>[key,input.value.trim()]));
    if(Object.values(routes).some(v=>v&&!/^-?[1-9][0-9]{0,19}$/.test(v))){status.textContent='Enter numeric chat IDs, including any leading minus sign.';return;}
    save.disabled=true;
    try{value=await request('/api/telegram-routing',{action:'save',revision:value.revision,routes});status.textContent='Saved. Connected scanners will refresh their routes.';}
    catch(e){status.textContent=e.message+' Reopen Settings to reload before retrying.';}
    finally{save.disabled=false;}
   });section.append(save);
  }
  section.append(status);
 }catch(e){if(isCurrent())section.append(node('p','Telegram routing unavailable. Only the installation owner can access online routing; the cloud migration must be installed.'));}
}
async function chasingControls(panel,isCurrent){
 if(!online)return;
 panel.append(node('p','Chasing is suspended. Chasing Telegram groups are inactive; historical settings are retained.'));
}
// Preserved for a future reviewed reactivation; not called by settings.
async function archivedChasingControls(panel,isCurrent){
 if(!online)return;
 const section=node('section');section.append(node('h3','Chasing'));panel.append(section);
 try{
  let value=await request('/api/chasing-settings');if(!isCurrent())return;
  if(!value.groups?.chasing||!value.groups?.live_positions)section.append(node('p','Your personal chase chats have not been connected yet. You can save your preferences now.'));
  const row=node('label','Stop chasing after favorable move (%) '),pct=node('input');
  pct.type='number';pct.min='0';pct.max='100';pct.step='any';pct.value=value.stop_pct;
  pct.setAttribute('aria-label','Stop chasing after favorable move (%)');row.append(pct);section.append(row);
  section.append(node('p','Measured from the original signal close for non-crypto chases. Crypto RSI watches end at 5m or expiry; price moves do not cancel them.'));
  const inputs={};
  for(const [key,label] of [['chasing','Receive chasing notifications'],['live_positions','Receive live-position notifications']]){
   const line=node('label'),input=node('input');input.type='checkbox';input.checked=value[key]===true;
   input.setAttribute('aria-label',label);line.append(input,document.createTextNode(' '+label));section.append(line);inputs[key]=input;
  }
  const status=node('p');status.setAttribute('role','status');
  section.append(button('Save chasing settings',async()=>{
   const stop_pct=Number(pct.value);
   if(!pct.value.trim()||!Number.isFinite(stop_pct)||stop_pct<=0||stop_pct>=100){status.textContent='Enter a percentage greater than zero and below 100.';return;}
   try{value=await request('/api/chasing-settings',{action:'save',revision:value.revision,stop_pct,chasing:inputs.chasing.checked,live_positions:inputs.live_positions.checked});status.textContent='Saved for new chases.';}catch(e){status.textContent=e.message;}
  }),status);
  for(const s of value.sessions||[]){section.append(node('p',`${s.root.symbol} · ${s.root.direction} · ${s.status} · stop ${s.stop_pct}%`));}
 }catch(e){section.append(node('p','Chasing settings unavailable: '+e.message));}
 try{
  const data=await request('/api/chasing-registration');if(!isCurrent())return;
  const admin=node('section'),member=node('select'),chase=node('input'),live=node('input'),status=node('p');
  admin.append(node('h3','Personal chase chats'),node('p','Owner-only rollout: create two private groups containing only the installation owner and the bot. Verify membership and identity before registering.'));
  for(const m of data.members||[]){const option=node('option',m.label);option.value=m.user_id;member.append(option);}
  function selectMember(){const m=(data.members||[]).find(m=>m.user_id===member.value);chase.value=m?.routes?.chasing||'';live.value=m?.routes?.live_positions||'';}
  member.onchange=selectMember;selectMember();
  for(const [label,input] of [['User',member],['Chasing chat ID',chase],['Live positions chat ID',live]]){const row=node('label',label+' ');row.append(input);admin.append(row);}
  admin.append(button('Register verified chats',async()=>{try{await request('/api/chasing-registration',{user_id:member.value,chasing:chase.value.trim(),live_positions:live.value.trim()});status.textContent='Registered. Personal notification choices remain separate.';}catch(e){status.textContent=e.message;}}),status);section.append(admin);
 }catch{/* Registration is owner-only. Personal settings remain available. */}
}
async function cryptoAutoChaseControls(panel,isCurrent){
 if(!online)return;
 panel.append(node('p','Automatic RSI + 5m RSI chasing is suspended.'));
}
// Preserved for a future reviewed reactivation; not called by settings.
async function archivedCryptoAutoChaseControls(panel,isCurrent){
 if(!online)return;
 const section=node('section');section.append(node('h3','Automatic crypto Chase'));panel.append(section);
 try{
  let value=await request('/api/crypto-auto-chase-settings');if(!isCurrent())return;
  const enabled=node('input'),chat=node('input'),status=node('p');enabled.type='checkbox';enabled.checked=value.enabled===true;
  enabled.setAttribute('aria-label','Receive automatic crypto Chase alerts');
  chat.type='text';chat.value=value.chat||'';chat.setAttribute('aria-label','Automatic crypto Chase chat ID');
  const choice=node('label','Receive automatic crypto Chase alerts ');choice.append(enabled);
  const destination=node('label','Verified group chat ID ');destination.append(chat);
  section.append(choice,destination,node('p','One watch per asset, shared with manual Chase. Lower timeframes take priority; alerts continue down to 5m or expiry. A 1h watch expires after 55 minutes.'));
  status.setAttribute('role','status');
  section.append(button('Save automatic crypto Chase',async()=>{
   const group=chat.value.trim();
   if((enabled.checked&&!group)||(group&&!/^-[1-9][0-9]{0,18}$/.test(group))){status.textContent='Enter a numeric group chat ID.';return;}
   try{value=await request('/api/crypto-auto-chase-settings',{action:'save',revision:value.revision,enabled:enabled.checked,chat:group});status.textContent='Saved. Applies to new crypto RSI alerts.';}catch(e){status.textContent=e.message;}
  }),status);
 }catch(e){if(isCurrent())section.append(node('p','Automatic crypto Chase settings unavailable: '+e.message));}
}
function settingsLoading(label){
 const status=node('p');status.className='settings-loading';status.setAttribute('role','status');
 const spinner=node('span');spinner.className='loading-spinner';spinner.setAttribute('aria-hidden','true');
 status.append(spinner,document.createTextNode(label));return status;
}
async function openVersions(){
 const dialog=$('settings-dialog'),panel=$('version-content'),id=++versionRequest;
 const isCurrent=()=>id===versionRequest&&dialog.open;
 const personal=node('div'),routing=node('div'),development=node('div'),chasing=node('div'),automatic=node('div'),versions=node('section');
 versions.append(node('h3','Versions'),settingsLoading('Checking versions…'));
 panel.replaceChildren(personal,chasing,automatic,routing,development,versions);
 if(!dialog.open)dialog.showModal();
 async function loadSettings(target,label,load){
  const loading=settingsLoading(label);target.append(loading);
  try{await load(target,isCurrent);}finally{if(isCurrent())target.replaceChildren(...Array.from(target.children).filter(child=>child!==loading));}
 }
 await Promise.all([
  loadSettings(personal,'Loading personal alerts…',personalTelegramControls),
  loadSettings(chasing,'Loading chasing settings…',chasingControls),
  loadSettings(automatic,'Loading automatic crypto Chase…',cryptoAutoChaseControls),
  loadSettings(routing,'Loading Telegram groups…',telegramRoutingControls),
  loadSettings(development,'Loading local settings…',developmentSettingsControls),
  loadVersionDetails(versions,isCurrent),
 ]);
}
async function loadVersionDetails(panel,isCurrent){
 try{
  let value;
  if(online){
   const [manifest,findings]=await Promise.all([fetch('/web/version.json',{cache:'no-store'}).then(r=>{if(!r.ok)throw Error('UI version unavailable');return r.json();}),request('/api/findings?source=live')]);
   value={environment:'Online dashboard',local:findings.scan?.versions||{},online_ui:manifest,scanner_checked_at:findings.scan?.scanned_at,scanner_environment:findings.scan?.environment};
  }else value=await request('/api/versions');
  if(!isCurrent())return;
  const local=value.local||{},github=value.github||{},remote=value.online_ui||{};
  panel.replaceChildren(node('p',`Environment: ${value.environment}`));
  if(!online){panel.append(node('p',value.environment==='prod'?'Production activity logs enabled.':'Development uses separate data; persistent activity logging and paper orders disabled.'));panel.append(node('p',`Telegram delivery: ${value.telegram_enabled?'enabled':'disabled (dry run)'}.`));panel.append(node('p','Storage: shared Postgres.'));}
  const rows=[['Local UI',local.ui||'Unavailable',versionMatch(local.ui,github.ui)],['Online UI',remote.ui||'Unavailable',versionMatch(remote.ui,github.ui)],['Local scanner',local.scanner||'Unavailable',versionMatch(local.scanner,github.scanner)],['GitHub UI',github.ui||'Unavailable','Latest default branch'],['GitHub scanner',github.scanner||'Unavailable','Latest default branch'],['Installed app',local.version||'Unavailable','Running version'],['Latest executable release',value.release?.version||'Unavailable','GitHub release']];
  panel.append(table(['Component','Version','Status'],rows));
  if(github.commit)panel.append(node('p','GitHub commit: '+github.commit.slice(0,12)));
  if(online)panel.append(node('p',`Local versions are reported by the ${value.scanner_environment||'local'} scanner’s last cloud sync${value.scanner_checked_at?' (scan '+displayTime(value.scanner_checked_at)+')':''}. Older scanners may not report versions yet. GitHub comparisons are available in the local dashboard.`));
  else if(!value.online_ui)panel.append(node('p','Set ONLINE_DASHBOARD_URL in .env to compare the deployed online UI.'));
  for(const [label,entry] of [['GitHub',value.github],['Release',value.release],['Online UI',value.online_ui]])if(entry?.error)panel.append(node('p',label+': '+entry.error));
  panel.append(node('p','A GitHub push updates source code. The executable takes an update after a newer vX.Y.Z release is published and installed. Component fingerprints detect changes even when the app version stays the same.'));
  const actions=node('div');actions.append(button('Refresh versions',openVersions));panel.append(actions);
  const notes=node('a','Changelog & FMP setup');notes.href='/web/update-notes.html';notes.target='_blank';notes.rel='noopener noreferrer';panel.append(notes);
  connectionTestControls(panel);
  if(value.updates?.supported){actions.append(button('Check for app update',async()=>{try{const result=await request('/api/updates',{action:'check'});message(result.message);}catch(e){message(e.message,true);}}));panel.append(node('p',value.updates.message||''));}
 }catch(e){if(isCurrent())panel.replaceChildren(node('p',e.message),button('Retry versions',openVersions));}
}
$('settings').onclick=openVersions;
$('close-settings').onclick=()=>$('settings-dialog').close();
$('settings-dialog').addEventListener('close',()=>{versionRequest++;});

async function watchCatchup(id,attempt=0){if(attempt>60)return;setTimeout(async()=>{try{const value=await request('/api/catchup');if(value.id!==id)return;if(value.status==='completed'||value.status==='failed')message(`Catch-up ${value.status}: ${value.findings||0} new findings, ${value.unavailable||0} unavailable histories. ${value.reason||''}`,value.status==='failed');else watchCatchup(id,attempt+1);}catch{}},10000);}

async function momentumFindings(source){
 content.replaceChildren(node('h2','Loading momentum…'),strategySelector(source));
 const data=await request('/api/momentum?source='+source);if(strategyViews[source]!=='momentum')return;
 content.replaceChildren(node('h2',source==='live'?'Daily momentum signals':'Momentum backtests'),strategySelector(source));
 content.append(node('p',data.note||''));
 if(source==='live'&&data.paper){const p=data.paper,st=p.status||{};content.append(node('p',`Paper trading: ${p.enabled?'enabled':'disabled'} · ${p.broker} · ${p.risk_pct}% risk · $${p.max_notional.toLocaleString()} position cap · equity ${st.equity==null?'—':'$'+st.equity.toLocaleString()} · checked ${st.checked_at?displayTime(st.checked_at):'pending'}`));}
 const latest=data.latest;
 if(source==='live'&&latest){
  content.append(node('p',`Scan: ${displayTime(latest.scanned_at)} · ${latest.status} · ${latest.version}`));
  content.append(table(['Market','Daily result','Qualified candidates'],(latest.results||[]).map(r=>[r.market,r.status,r.candidates])));
  if(Date.now()-latest.scanned_at>36*3600000)content.append(node('p','Scan is stale. Check the local worker and cloud sync.'));
 }
 if(source==='backtest'){
  const m=data.metrics||{};
  content.append(node('p',`${m.candidates??0} candidates · ${m.closed_trades??0} closed trades · win rate ${m.win_rate_pct==null?'—':m.win_rate_pct.toFixed(1)+'%'} · average net return ${m.average_net_pct==null?'—':m.average_net_pct.toFixed(2)+'%'}`));
  if(data.generated_at)content.append(node('p','Last replay: '+displayTime(data.generated_at)));
  content.append(node('p','Momentum backtests refresh from daily scan history. The RSI replay controls apply to the RSI view.'));
  content.append(exportCSVButton('momentum-backtest-results',async()=>data.signals||[]));
 }
 const signals=data.signals||[],market=node('select');market.setAttribute('aria-label','Momentum market');
 for(const [key,name] of [['all','Stocks and crypto'],['stock','US stocks'],['crypto','Crypto']]){const o=node('option',name);o.value=key;market.append(o);}
 const panel=node('section');content.append(market,panel);
 const price=v=>Number(v.toPrecision(7));
 function render(){panel.replaceChildren(table(['Market / asset','Direction / confirmation','Entry range','Stop / target','Evidence','Outcome','Actions'],signals.filter(s=>market.value==='all'||s.market===market.value).map(s=>{
  const f=s.outcome||{},out=node('div');out.append(node('strong',f.status||'Awaiting evaluation'));if(f.net_pct!=null)out.append(node('p',`Net ${f.net_pct.toFixed(2)}%`));
  if(s.paper){const p=s.paper;out.append(node('p','Alpaca paper: '+p.state));if(p.reason)out.append(node('small',p.reason));if(p.gross_return_pct!=null)out.append(node('small',`Paper gross ${p.gross_return_pct.toFixed(2)}% · P/L $${p.gross_pnl.toFixed(2)} before fees`));if(p.order?.filled_avg_price)out.append(node('small',`Filled ${p.order.filled_qty} at ${p.order.filled_avg_price}`));if(p.sizing)out.append(node('small',`Planned risk $${p.sizing.risk_dollars.toFixed(2)} · value $${p.sizing.notional.toFixed(2)}`));}
  const entry=node('div');entry.append(node('strong',`${price(s.entry_min)} – ${price(s.entry_max)}`),node('small','Next session open only; skip outside range or below 1.5 reward/risk.'));if(s.entry_open_at)entry.append(node('small','Entry session: '+displayTime(s.entry_open_at)+' · expires '+displayTime(s.expires_at)));
  return [s.market+' · '+s.symbol,s.direction.toUpperCase()+' · '+displayTime(s.confirmed_at),entry,`${price(s.stop)} / ${price(s.target)}`,`ROC ${s.roc_pct.toFixed(2)}% · efficiency ${s.efficiency.toFixed(2)} · extension ${s.extension_atr.toFixed(2)} ATR · ${s.version}`,out,manageAssetButton(s)];
 })));if(!signals.length)panel.append(node('p','No momentum signals available. Review the daily result and coverage.'));}
 market.onchange=render;render();
 if(source==='live'&&latest){const details=node('details');details.append(node('summary','Momentum scan coverage'),table(['Market','Asset','Status','Details'],latest.coverage.map(r=>[r.market,r.symbol||'Broad universe',r.status,r.reason||(r.eligible!=null?`${r.eligible} eligible histories`:r.candles?`${r.candles} closed daily candles`:'')])));content.append(details);}
}

async function pinBarFindings(strategy='pin-bars'){
 const combined=strategy==='rsi-pb',bb=strategy==='bb-pin-bars',title=combined?'RSI + PB':bb?'BB Pin-Bars':'Pin-Bars';
 content.replaceChildren(node('h2',`Loading ${title}…`),strategySelector('live'));
 const data=await request('/api/'+strategy+'?page='+page);if(strategyViews.live!==strategy)return;
 content.replaceChildren(node('h2',`${title} signals`),strategySelector('live'));
 const wickLimit=Number.isFinite(data.rules.max_short_wick_ratio)?` · short wick ≤ ${data.rules.max_short_wick_ratio*100}% of long wick`:'';
 content.append(node('p',combined?'RSI Divergence and PinBars · next candle only · opposite wick shape · no EMA filters · direction follows RSI.':bb?`BB ${data.rules.bb_period}/${data.rules.bb_multiplier} · wick > ${data.rules.ratio} × body${wickLimit} · bullish lower-BB touch or bearish upper-BB touch · no EMA filters.`:`EMA ${data.rules.fast}/${data.rules.slow} · wick > ${data.rules.ratio} × body${wickLimit} · touch either EMA · confirmed at the signal candle close.`));
 content.append(node('p',data.scanned_at?'Latest scan: '+displayTime(data.scanned_at):'Awaiting the first scan.'));
 if(online)content.append(node('p',`Hosted results show up to the latest ${data.mirror_limit??500} synced signals.`));
 const rows=data.signals.map(s=>{const evidence=node('details');evidence.append(node('summary','Candle evidence'),node('p',`OHLC: ${s.open} / ${s.high} / ${s.low} / ${s.close}`),node('p',pinBarEvidence(s)),node('p',`Source: ${s.source} · candle: ${displayTime(s.candle_at)} · ${s.rules.seed} · version: ${s.version}`));return [s.symbol,s.timeframe,s.direction.toUpperCase(),displayTime(s.confirmed_at),evidence,manageAssetButton(s)];});
 content.append(table(['Asset','Timeframe','Direction','Confirmed','Evidence','Actions'],rows));
 if(!rows.length)content.append(node('p',`No new ${title} signals recorded. The first successful scan establishes a baseline.`));
 const nav=node('div');if(page>0)nav.append(button('Previous',()=>{page--;pinBarFindings(strategy).catch(e=>message(e.message,true));}));if(data.has_more)nav.append(button('Next',()=>{page++;pinBarFindings(strategy).catch(e=>message(e.message,true));}));content.append(nav);
 content.append(node('h3',`${title} coverage`),table(['Asset','Timeframe','Provider','Status','Latest candle','Details'],data.coverage.map(r=>[r.asset,r.timeframe,r.provider,r.check_state||r.status,r.last_closed_at?displayTime(r.last_closed_at):'—',[r.reason||`${r.waiting_setups||0} waiting setups · ${r.candles||0} closed candles · ${r.new_signals||0} new signals${r.limited_history?' · limited indicator history':''}`,r.data_notes].filter(Boolean).join(' · ')])));
}

let csvReportId=null,csvPage=0;
async function uploadCSV(){
 const dialog=$('backtest-dialog'),form=$('backtest-form');form.replaceChildren(node('h3','Upload candles & backtest'));
 const file=node('input');file.type='file';file.accept='.csv,text/csv';file.required=true;file.setAttribute('aria-label','Candle CSV file');
 const symbol=node('input');symbol.required=true;symbol.placeholder='Exact symbol, e.g. TVC:USOIL';symbol.setAttribute('aria-label','CSV instrument');
 const frame=node('select');frame.setAttribute('aria-label','CSV timeframe');for(const key of frames){const o=node('option',key);o.value=key;frame.append(o);}frame.value='4h';
 const strategy=node('select');strategy.setAttribute('aria-label','CSV strategy');for(const [key,label] of [['pin-bars','Pin-Bars'],['bb-pin-bars','BB Pin-Bars'],['rsi-pb','RSI + PB'],['confirmed','Confirmed RSI divergence'],['warmup','Warm-up RSI divergence']]){const o=node('option',label);o.value=key;strategy.append(o);}strategy.value=['pin-bars','bb-pin-bars','rsi-pb'].includes(strategyViews.backtest)?strategyViews.backtest:'confirmed';
 file.onchange=()=>{const name=file.files?.[0]?.name||'';symbol.value=name.split(',')[0].replace('_',':').replace(/\.csv$/i,'');const match=name.match(/,\s*(60|240|1D|1W|1M|D|W|M)_/i);if(match)frame.value=({60:'1h',240:'4h','1D':'daily',D:'daily','1W':'weekly',W:'weekly','1M':'monthly',M:'monthly'})[match[1].toUpperCase()];};
 form.append(node('label','CSV file'),file,node('label','Instrument'),symbol,node('label','Candle timeframe'),frame,node('label','Strategy'),strategy,node('p','Replay dates come from the CSV. Supports TradingView time/open/high/low/close exports and timestamp/closed_at candle files. Timestamps need a timezone or Unix time. Indicator columns are ignored. Maximum 2 MB / 20,000 candles.'));
 const market=node('select');market.setAttribute('aria-label','Market calendar');for(const [key,title] of [['crypto','Continuous / crypto'],['forex','Forex'],['stock','US stocks / ETFs']]){const option=node('option',title);option.value=key;market.append(option);}form.append(node('label','Market calendar'),market,node('p','For RSI + PB, choose the source market so weekends and exchange holidays are handled correctly.'));
 const run=node('button','Upload & backtest');run.type='submit';form.append(run);form.onsubmit=async e=>{e.preventDefault();const selected=file.files?.[0];if(!selected){message('Choose a CSV file',true);return;}if(selected.size>2000000){message('CSV must be at most 2 MB',true);return;}run.disabled=true;try{const data=await request('/api/csv-backtests',{csv:await selected.text(),filename:selected.name,symbol:symbol.value.trim(),timeframe:frame.value,strategy:strategy.value,market:market.value});csvReportId=data.report.id;csvPage=0;strategyViews.backtest=['pin-bars','bb-pin-bars','rsi-pb'].includes(data.report.strategy)?data.report.strategy:'rsi';dialog.close();await csvBacktestFindings();message('CSV backtest saved.');}catch(e){message(e.message,true);run.disabled=false;}};dialog.showModal();
}
async function csvBacktestFindings(strategy=null,supplied=null){
 const data=supplied||await request('/api/csv-backtests?'+new URLSearchParams({page:csvPage,...(csvReportId?{id:csvReportId}:{}),...(strategy?{strategy}: {})}));
 content.replaceChildren(node('h2','CSV backtests'),strategySelector('backtest'),button('Upload CSV & backtest',uploadCSV));
 const picker=node('select');picker.setAttribute('aria-label','Saved CSV backtest');const choose=node('option','Choose a saved CSV backtest');choose.value='';picker.append(choose);for(const r of data.reports||[]){const o=node('option',`${r.symbol} · ${r.timeframe} · ${r.strategy} · ${r.filename}`);o.value=r.id;picker.append(o);}picker.value=data.report?.id||'';picker.onchange=()=>{csvReportId=picker.value||null;csvPage=0;csvBacktestFindings().catch(e=>message(e.message,true));};content.append(picker);
 const report=data.report;if(report)csvReportId=report.id;if(!report){content.append(node('p','No CSV backtest saved for this strategy. Upload a candle file to replay its dates.'));return;}
 strategyViews.backtest=['pin-bars','bb-pin-bars','rsi-pb'].includes(report.strategy)?report.strategy:'rsi';
 content.append(exportCSVButton('backtest-'+report.symbol+'-'+report.timeframe+'-'+report.strategy,async()=>(await allResultPages('/api/csv-backtests?id='+encodeURIComponent(report.id),'report')).map(s=>({symbol:report.symbol,timeframe:report.timeframe,strategy:report.strategy,report_id:report.id,filename:report.filename,...s}))));
 content.append(node('h3',`${report.symbol} · ${report.timeframe} · ${report.strategy}`),node('p',`${displayTime(report.start_at)} → ${displayTime(report.end_at)} · ${report.candles} completed candles · ${report.signal_count} signals · ${report.excluded_open_candles} unfinished candles excluded`),node('p',`Initial ${report.warmup_candles} candles provide indicator warm-up. ${report.limited_history?'Limited history. ':''}${report.alignment}.`),node('p',report.note));
 const movement=(s,h)=>{const f=s.forward?.[h];return f?.status==='complete'?`${f.directional_close_pct.toFixed(2)}% direction-adjusted close · favorable ${f.favorable_pct.toFixed(2)}% · adverse ${f.adverse_pct.toFixed(2)}%`:'Incomplete forward history';};
 content.append(table(['Confirmed','Timeframe','Direction','Candle','Evidence','Next 5 candles','Next 10 candles','Actions'],report.signals.map(s=>[displayTime(s.confirmed_at),s.timeframe||report.timeframe,s.direction.toUpperCase(),candleDrawing(s),['pin-bars','bb-pin-bars','rsi-pb'].includes(s.strategy)?pinBarEvidence(s):`Price ${s.price1} → ${s.price2} · RSI ${s.rsi1.toFixed(2)} → ${s.rsi2.toFixed(2)}`,movement(s,5),movement(s,10),manageAssetButton({...s,symbol:report.symbol})])));
 const nav=node('div');if(csvPage>0)nav.append(button('Previous',()=>{csvPage--;csvBacktestFindings().catch(e=>message(e.message,true));}));if(report.has_more)nav.append(button('Next',()=>{csvReportId=report.id;csvPage++;csvBacktestFindings().catch(e=>message(e.message,true));}));content.append(nav);
}

function candleDrawing(s){
 const {open,high,low,close}=s;
 if(![open,high,low,close].every(Number.isFinite)||low>Math.min(open,close)||high<Math.max(open,close))return node('small','OHLC unavailable');
 const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 64 96');svg.setAttribute('width','64');svg.setAttribute('height','96');svg.setAttribute('role','img');svg.setAttribute('aria-label',`Signal candle: open ${open}, high ${high}, low ${low}, close ${close}`);
 const title=document.createElementNS('http://www.w3.org/2000/svg','title');title.textContent=`Open ${open} · High ${high} · Low ${low} · Close ${close}. Each candle is scaled to its own high–low range.`;svg.append(title);
 const y=price=>high===low?48:10+76*(high-price)/(high-low),color=close>=open?'#34d399':'#f87171';
 const shape=(tag,attrs)=>{const n=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [key,value] of Object.entries(attrs))n.setAttribute(key,String(value));svg.append(n);};
 shape('line',{x1:32,x2:32,y1:y(high),y2:y(low),stroke:color,'stroke-width':2});
 if(open===close)shape('line',{x1:22,x2:42,y1:y(open),y2:y(close),stroke:color,'stroke-width':3});
 else shape('rect',{x:22,y:Math.min(y(open),y(close)),width:20,height:Math.max(1,Math.abs(y(open)-y(close))),fill:color,rx:1});
 return svg;
}

function pinBarEvidence(s){
 if(s.strategy==='rsi-pb')return `Opposite pinbar: ${s.pinbar_direction} · body: ${s.body} · lower wick: ${s.lower_wick} · upper wick: ${s.upper_wick} · divergence candle: ${displayTime(s.pivot2)} → ${displayTime(s.pivot_closed_at)} · pinbar candle: ${displayTime(s.candle_at)} → ${displayTime(s.confirmed_at)} · `+s.divergences.map(r=>`Reference ${displayTime(r.pivot1)} → ${displayTime(r.pivot1_closed_at||r.pivot_closed_at)} · Price ${r.price1} → ${r.price2} · RSI ${r.rsi1.toFixed(2)} → ${r.rsi2.toFixed(2)} · BB ${r.band2} · touch ${r.band_touch_price}`).join(' | ');
 const location=s.strategy==='bb-pin-bars'?`Lower BB: ${s.lower_bb} · upper BB: ${s.upper_bb}`:`Fast EMA: ${s.fast_ema} · slow EMA: ${s.slow_ema}`;
 return `${location} · body: ${s.body} · lower wick: ${s.lower_wick} · upper wick: ${s.upper_wick}${Number.isFinite(s.rules?.max_short_wick_ratio)?` · short wick limit: ${s.rules.max_short_wick_ratio*100}% of long wick`:""}`;
}
