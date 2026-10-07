// Supabase Edge Function. Configure SYNC_TOKEN and INSTALLATION_ID as function secrets.
const url=Deno.env.get('SUPABASE_URL'),key=Deno.env.get('SUPABASE_SERVICE_ROLE_KEY');
const headers={apikey:key,Authorization:`Bearer ${key}`,'Content-Type':'application/json'};
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
  const query=new URLSearchParams({installation:`eq.${installation}`,kind:'eq.feedback_history',change_sequence:`gt.${body.cursor}`,order:'change_sequence.asc',limit:'100',select:'payload,change_sequence'});
  const feedback=await fetch(url+'/rest/v1/dashboard_records?'+query,{headers});if(!feedback.ok)return new Response('Storage read failed',{status:503});
  result.feedback=await feedback.json();
  const stateQuery=new URLSearchParams({installation:`eq.${installation}`,kind:'eq.finding_state',change_sequence:`gt.${stateCursor}`,order:'change_sequence.asc',limit:'100',select:'payload,change_sequence'});
  const states=await fetch(url+'/rest/v1/dashboard_records?'+stateQuery,{headers});if(!states.ok)return new Response('Storage read failed',{status:503});
  result.finding_states=await states.json();return Response.json(result);
 }catch{return new Response('Invalid sync request',{status:400});}
});
