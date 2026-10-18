// Machine-only coordination. Telegram credentials remain on the sending device.
const url=Deno.env.get('SUPABASE_URL'),key=Deno.env.get('SUPABASE_SERVICE_ROLE_KEY');
Deno.serve(async request=>{
 const expected=Deno.env.get('SYNC_TOKEN'),installation=Deno.env.get('INSTALLATION_ID');
 if(!expected||request.headers.get('Authorization')!==`Bearer ${expected}`)return new Response('Unauthorized',{status:401});
 if(request.method!=='POST')return new Response('Method not allowed',{status:405});
 if(!installation||!url||!key)return new Response('Coordination unavailable',{status:503});
 let input;
 try{
  const raw=await request.text();if(raw.length>4096)return new Response('Request too large',{status:413});
  input=JSON.parse(raw);
  if(!input||!['acquire','renew','complete','release'].includes(input.action)
   ||['destination_hash','event_hash','body_hash'].some(k=>typeof input[k]!=='string'||! /^[a-f0-9]{64}$/.test(input[k]))
   ||['owner','sender_id'].some(k=>typeof input[k]!=='string'||! /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(input[k]))
   ||!['dev','prod'].includes(input.environment)||typeof input.device!=='string'||!input.device.length||input.device.length>80
   ||(input.message_id!=null&&(!Number.isSafeInteger(input.message_id)||input.message_id<0)))throw Error();
  // Whitelist fields; installation is always derived from the function secret.
  input=Object.fromEntries(['action','destination_hash','event_hash','body_hash','owner','sender_id','environment','device','message_id'].filter(k=>k in input).map(k=>[k,input[k]]));
 }catch{return new Response('Invalid coordination request',{status:400});}
 try{
  const response=await fetch(url+'/rest/v1/rpc/telegram_coordinate',{method:'POST',headers:{apikey:key,Authorization:`Bearer ${key}`,'Content-Type':'application/json'},body:JSON.stringify({installation,input}),signal:AbortSignal.timeout(8000)});
  if(!response.ok)return new Response('Coordination unavailable',{status:503});
  return Response.json(await response.json());
 }catch{return new Response('Coordination unavailable',{status:503});}
});
