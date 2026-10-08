// Supabase Edge Function. Configure SYNC_TOKEN and INSTALLATION_ID as function secrets.
const url=Deno.env.get('SUPABASE_URL'),key=Deno.env.get('SUPABASE_SERVICE_ROLE_KEY');
const headers={apikey:key,Authorization:`Bearer ${key}`,'Content-Type':'application/json'};
async function publishSyncedBacktest(installation,reportRecord){
 const report=reportRecord.payload,ids=[...new Set(report.finding_ids)];
 if(!/^[a-f0-9]{64}$/.test(reportRecord.key)||ids.some(id=>typeof id!=='string'||!/^[a-f0-9]{64}$/.test(id)))throw Error('Invalid synced backtest identifiers');
 const reportId=`${reportRecord.key.slice(0,8)}-${reportRecord.key.slice(8,12)}-${reportRecord.key.slice(12,16)}-${reportRecord.key.slice(16,20)}-${reportRecord.key.slice(20,32)}`;
 const existingUrl=new URLSearchParams({installation:`eq.${installation}`,id:`eq.${reportId}`,select:'id',limit:'1'});
 const existingResponse=await fetch(url+'/rest/v1/dashboard_backtest_jobs?'+existingUrl,{headers});
 if(!existingResponse.ok)throw Error('Backtest publication lookup failed');
 if((await existingResponse.json()).length)return 'already published';
 const generatedAt=Number(report.generated_at)*1000;
 if(!Number.isFinite(generatedAt)||!Array.isArray(report.summary)||!Array.isArray(report.results))throw Error('Invalid synced backtest report');
 for(let offset=0;offset<ids.length;offset+=100){
  const query=new URLSearchParams({installation:`eq.${installation}`,kind:'eq.backtest',key:`in.(${ids.slice(offset,offset+100).join(',')})`,select:'key',limit:'100'});
  const response=await fetch(url+'/rest/v1/dashboard_records?'+query,{headers});
  if(!response.ok)throw Error('Backtest publication verification failed');
  if((await response.json()).length!==ids.slice(offset,offset+100).length)return 'waiting for synced findings';
 }
 const input={assets:[...new Set(report.results.map(r=>r.asset))],timeframes:[...new Set(report.results.map(r=>r.timeframe))],strategy:'confirmed',source:'local',year:report.year,report_id:reportRecord.key};
 const response=await fetch(url+'/rest/v1/dashboard_backtest_jobs?on_conflict=id',{method:'POST',headers:{...headers,Prefer:'resolution=merge-duplicates,return=minimal'},body:JSON.stringify([{id:reportId,installation,status:'completed',input,summary:report.summary,finding_ids:ids,updated_at:new Date(generatedAt).toISOString()}])});
 if(!response.ok)throw Error('Backtest publication failed');
 return 'published';
}
async function publishSyncedBacktests(installation,records){
 const reports=records.filter(r=>r.kind==='backtest_run'&&Array.isArray(r.payload?.finding_ids));
 const results=[];
 for(const report of reports)results.push({report_id:report.key,status:await publishSyncedBacktest(installation,report)});
 return results;
}
Deno.serve(async request=>{
 const expected=Deno.env.get('SYNC_TOKEN');
 if(!expected||request.headers.get('Authorization')!==`Bearer ${expected}`)return new Response('Unauthorized',{status:401});
 if(request.method!=='POST')return new Response('Method not allowed',{status:405});
 try{
  const raw=await request.text();if(raw.length>262144)return new Response('Batch too large',{status:413});
  const body=JSON.parse(raw),installation=Deno.env.get('INSTALLATION_ID');
  const stateCursor=body.state_cursor??0;if(!Number.isSafeInteger(stateCursor)||stateCursor<0)throw Error('Invalid state cursor');
  if(!Array.isArray(body.records)||body.records.length>100||!Number.isSafeInteger(body.cursor)||body.cursor<0)throw Error('Invalid batch');
  const response=await fetch(url+'/rest/v1/rpc/dashboard_ingest',{method:'POST',headers,body:JSON.stringify({installation,records:body.records,config_base:body.config_base||0})});
  if(!response.ok)return new Response('Storage write failed',{status:503});
  const result=await response.json();
  result.backtest_publications=await publishSyncedBacktests(installation,body.records);
  const query=new URLSearchParams({installation:`eq.${installation}`,kind:'eq.feedback_history',change_sequence:`gt.${body.cursor}`,order:'change_sequence.asc',limit:'100',select:'payload,change_sequence'});
  const feedback=await fetch(url+'/rest/v1/dashboard_records?'+query,{headers});if(!feedback.ok)return new Response('Storage read failed',{status:503});
  result.feedback=await feedback.json();
  const stateQuery=new URLSearchParams({installation:`eq.${installation}`,kind:'eq.finding_state',change_sequence:`gt.${stateCursor}`,order:'change_sequence.asc',limit:'100',select:'payload,change_sequence'});
  const states=await fetch(url+'/rest/v1/dashboard_records?'+stateQuery,{headers});if(!states.ok)return new Response('Storage read failed',{status:503});
  result.finding_states=await states.json();return Response.json(result);
 }catch{return new Response('Invalid sync request',{status:400});}
});
