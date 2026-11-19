// Clerk mode is opt-in. Tokens remain in Clerk's session, never our storage.
(function () {
 function origin(value) {
  const url=new URL(value);
  if((url.protocol!=='https:'&&!(url.protocol==='http:'&&['localhost','127.0.0.1','[::1]'].includes(url.hostname)))||url.username||url.password||url.pathname!=='/'||url.search||url.hash)throw Error('Invalid dashboard API origin');
  return url.origin;
 }
 function bundle(document,url,key){return new Promise((resolve,reject)=>{
  const script=document.createElement('script');script.src=url;script.async=true;script.crossOrigin='anonymous';
  if(key)script.setAttribute('data-clerk-publishable-key',key);
  script.onload=resolve;script.onerror=()=>reject(Error('Unable to load secure sign-in. Please retry.'));document.head.appendChild(script);
 });}
 window.createClerkDashboard=async function(config,{document=window.document,onReady,onIdentityChange}={}){
  const key=config.clerkPublishableKey;
  if(!/^pk_(test|live)_[A-Za-z0-9=]+$/.test(key||''))throw Error('Missing Clerk publishable key');
  const decoded=atob(key.split('_')[2]);
  if(!decoded.endsWith('$')||!/^([a-z0-9-]+\.)+[a-z0-9-]+\$$/i.test(decoded))throw Error('Invalid Clerk frontend domain');
  const domain=decoded.slice(0,-1),apiOrigin=origin(config.apiOrigin);
  await bundle(document,`https://${domain}/npm/@clerk/ui@1/dist/ui.browser.js`);
  await bundle(document,`https://${domain}/npm/@clerk/clerk-js@6/dist/clerk.browser.js`,key);
  const clerk=window.Clerk;
  await clerk.load({ui:{ClerkUI:window.__internal_ClerkUICtor}});
  let sessionId=clerk.session?.id||null,generation=0;
  clerk.addListener(({session})=>{
   const next=session?.id||null;
   if(next!==sessionId){sessionId=next;generation++;onIdentityChange();}
  });
  const session={
   signedIn:()=>Boolean(clerk.session),
   async request(path,body,timeout=15000){
    if(!path.startsWith('/api/')||path.includes('#'))throw Error('Invalid API path');
    const initial=generation,sid=clerk.session?.id;
    if(!sid)throw Error('Please sign in.');
    const token=await clerk.session.getToken();
    if(!token||initial!==generation||clerk.session?.id!==sid)throw Error('Your session changed. Please retry.');
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),timeout);
    try{
     const response=await fetch(apiOrigin+'/api/dashboard',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify({installation:config.installationId,operation:path,input:body||{}}),signal:controller.signal,credentials:'omit',redirect:'error'});
     const result=await response.json();
     if(initial!==generation||clerk.session?.id!==sid)throw Error('Your session changed. Please retry.');
     if(!response.ok||result.error)throw Error(response.status===401?'Your session expired. Please sign in again.':result.error||'Request failed');
     return result;
    }finally{clearTimeout(timer);}
   }
  };
  const login=document.getElementById('login');login.replaceChildren();login.hidden=session.signedIn();
  if(session.signedIn())clerk.mountUserButton(document.getElementById('clerk-account'));
  else clerk.mountSignIn(login);
  onReady(session);
  return session;
 };
})();
