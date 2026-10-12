import {replayCSV} from './engine.mjs';
const url=Deno.env.get('SUPABASE_URL'),key=Deno.env.get('SUPABASE_SERVICE_ROLE_KEY');
const cors={'Access-Control-Allow-Origin':'*','Access-Control-Allow-Headers':'authorization,apikey,content-type','Access-Control-Allow-Methods':'POST,OPTIONS'};
Deno.serve(async request=>{
 if(request.method==='OPTIONS')return new Response(null,{headers:cors});
 if(request.method!=='POST')return new Response('Method not allowed',{status:405,headers:cors});
 try{
  const auth=request.headers.get('Authorization')||'';
  const user=await fetch(url+'/auth/v1/user',{headers:{apikey:key,Authorization:auth}});
  if(!user.ok)return Response.json({error:'Sign in required'},{status:401,headers:cors});
  const raw=await request.text();if(new TextEncoder().encode(raw).length>2100000)return Response.json({error:'Upload too large (2 MB maximum)'},{status:413,headers:cors});
  const body=JSON.parse(raw),installation=body.installation;
  const authorized=await fetch(url+'/rest/v1/rpc/dashboard_api',{method:'POST',headers:{apikey:key,Authorization:auth,'Content-Type':'application/json'},body:JSON.stringify({installation,operation:'/api/csv-backtests',input:{action:'authorize'}})});
  if(!authorized.ok||(await authorized.json()).authorized!==true)return Response.json({error:'Editor access required'},{status:403,headers:cors});
  const report=await replayCSV(body.input);
  const saved=await fetch(url+'/rest/v1/rpc/dashboard_save_csv_backtest',{method:'POST',headers:{apikey:key,Authorization:`Bearer ${key}`,'Content-Type':'application/json'},body:JSON.stringify({installation,report})});
  if(!saved.ok)return Response.json({error:'Unable to save CSV backtest'},{status:503,headers:cors});
  return Response.json({report:{...report,signals:report.signals.slice(0,50),has_more:report.signals.length>50},reports:[]},{headers:cors});
 }catch(e){return Response.json({error:e instanceof Error?e.message:'Invalid CSV backtest request'},{status:400,headers:cors});}
});
