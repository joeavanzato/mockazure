import {api,el,setCSRF,heading,button} from './api.js';
import {icon,empty} from './components.js';
import {parseRoute,routeHash} from './state.js';
import {resources} from './resources.js';
import {workbench} from './workbench.js';
import {profiles} from './profiles.js';
import {runs} from './runs.js';
import {inspector} from './inspector.js';
import {hunting} from './hunting.js';
const views={resources,workbench,profiles,runs,inspector,hunting,admin};
const labels={resources:'Explore resources',workbench:'API workbench',profiles:'Compose lab',runs:'Population runs',hunting:'Hunt evidence',inspector:'Inspector',admin:'Console admin'};
const root=document.querySelector('#workspace'),tenantSelect=document.querySelector('#tenant'),drafts=new Map();
let tenants=[],after=null,cleanup=null,controller=null,generation=0,session=null,active=false;
function notice(message,error=false){const box=document.querySelector('#notice');box.textContent=message;box.className=error?'error':'';box.hidden=!message;}
function navigate(value,{replace=false}={}){const hash=routeHash(value);if(replace){history.replaceState(null,'',hash);show();}else if(location.hash===hash)show();else location.hash=hash;}
async function loadTenants(more=false){
 const page=await api('/_mock/tenants?limit=100'+(more&&after?'&after='+encodeURIComponent(after):''));
 tenants=more?[...tenants,...page.items]:page.items;after=page.next_after;
 tenantSelect.replaceChildren(el('option',{value:''},'Select a tenant'),...tenants.map(t=>el('option',{value:t.id},t.display_name)));
 const target=parseRoute(location.hash).tenant;
 // A direct link may refer to a later tenant page. Resolve it using the actual bounded catalog.
 while(target&&!tenants.some(t=>t.id===target)&&after){const next=await api('/_mock/tenants?limit=100&after='+encodeURIComponent(after));after=next.next_after;tenants.push(...next.items);for(const t of next.items)tenantSelect.append(el('option',{value:t.id},t.display_name));}
 tenantSelect.value=target||'';document.querySelector('#more-tenants').hidden=!after;
}
function nav(){
 for(const id of ['primary-nav','server-nav'])document.getElementById(id).replaceChildren();
 for(const [key,label]of Object.entries(labels)){
  if(key==='inspector'&&!session.inspector_enabled)continue;
  if(key==='hunting'&&!session.capabilities?.hunting)continue;
  const b=button([icon(key),label],()=>{const route=parseRoute(location.hash);navigate({view:key,tenant:route.tenant});});b.dataset.view=key;
  document.getElementById(['admin','inspector'].includes(key)?'server-nav':'primary-nav').append(b);
 }
}
async function show(){
 if(!active)return;
 const own=++generation;controller?.abort();cleanup?.();cleanup=null;controller=new AbortController();
 const modal=document.querySelector('#editor');modal.onclose=null;if(modal.open)modal.close();
 const route=parseRoute(location.hash),tenant=tenants.find(t=>t.id===route.tenant)||null;
 tenantSelect.value=tenant?.id||'';document.querySelector('#view-title').textContent=labels[route.view];notice('');
 document.querySelectorAll('[data-view]').forEach(b=>{if(b.dataset.view===route.view)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});
 const panel=el('div',{},el('p',{class:'muted',role:'status'},'Loading workspace…'));root.replaceChildren(panel);
 const signal=controller.signal;
 const ctx={route,tenant:()=>tenant,capabilities:session.capabilities||{},drafts,signal,active:()=>active&&own===generation,
  api:(path,options={})=>api(path,{...options,signal}),
  notice:(m,e=false)=>{if(own===generation)notice(m,e);},
  error:error=>{if(own===generation&&error.name!=='AbortError')notice(error.message||String(error),true);},
  refreshTenants:()=>loadTenants(),runId:()=>route.run,
  go:(view,params={})=>navigate({view,tenant:tenant?.id,...params}),
  push:params=>{history.pushState(null,'',routeHash({...route,...params}));},
  replace:params=>{history.replaceState(null,'',routeHash({...route,...params}));},
  openRun:(id,tenantId=tenant?.id)=>navigate({view:'runs',tenant:tenantId,run:id})};
 try{
  if(route.tenant&&!tenant){panel.replaceChildren(empty('Tenant unavailable','Refresh the tenant catalog or select another tenant.'));return;}
  if(route.view==='inspector'&&!session.inspector_enabled){panel.replaceChildren(empty('Inspector is disabled','Enable the curated read-only Inspector in the instance configuration.'));return;}
  if(route.view==='hunting'&&!session.capabilities?.hunting){panel.replaceChildren(empty('Hunting is unavailable','This instance does not expose the hunting console capability.'));return;}
  const returned=await views[route.view](panel,ctx);if(own===generation)cleanup=returned;else returned?.();
 }catch(error){if(own===generation&&error.name!=='AbortError'){ctx.error(error);panel.replaceChildren(empty('Unable to load this workspace',error.message),button('Retry',show));}}
}
function signedOut(){active=false;controller?.abort();cleanup?.();cleanup=null;++generation;drafts.clear();setCSRF(null);const modal=document.querySelector('#editor');modal.onclose=null;if(modal.open)modal.close();root.replaceChildren();document.querySelector('#lab').hidden=true;document.querySelector('#login').hidden=false;document.querySelector('#password').value='';}
async function signedIn(){session=await api('/_mock/console/session');setCSRF(session.csrf_token);await loadTenants();nav();active=true;document.querySelector('#login').hidden=true;document.querySelector('#lab').hidden=false;await show();}
async function admin(panel){panel.replaceChildren(heading('Your console session.','Shared lab administration is separate from named Microsoft API identities.'),el('section',{class:'panel'},el('h2',{},'Lab administration'),el('p',{},'This session can compose synthetic tenants and inspect service data. Public API operations still enforce the chosen test identity’s permissions.'),el('p',{class:'hint'},'Session expires: '+new Date(session.expires_at).toLocaleString()),el('p',{class:'hint'},'Inspector: '+(session.inspector_enabled?'enabled, read only':'disabled')),button('Sign out',logout)));}
async function logout(){await api('/_mock/console/logout',{method:'POST'});signedOut();}
document.querySelector('#login-form').addEventListener('submit',async event=>{event.preventDefault();const submit=event.currentTarget.querySelector('button');submit.disabled=true;submit.textContent='Signing in…';document.querySelector('#login-error').textContent='';try{const result=await api('/_mock/console/login',{method:'POST',anonymous:true,json:{password:document.querySelector('#password').value}});setCSRF(result.csrf_token);document.querySelector('#password').value='';await signedIn();}catch(error){document.querySelector('#login-error').textContent=error.message;}finally{submit.disabled=false;submit.textContent='Sign in';}});
tenantSelect.addEventListener('change',()=>{const route=parseRoute(location.hash);navigate({view:route.view,tenant:tenantSelect.value,family:route.family});});
document.querySelector('#more-tenants').addEventListener('click',()=>loadTenants(true).catch(e=>notice(e.message,true)));
document.querySelector('#refresh-tenants').addEventListener('click',async()=>{const b=document.querySelector('#refresh-tenants');b.disabled=true;controller?.abort();cleanup?.();++generation;try{await loadTenants();await show();}catch(e){notice(e.message,true);}finally{b.disabled=false;}});
document.querySelector('#sign-out').addEventListener('click',()=>logout().catch(e=>notice(e.message,true)));
document.querySelector('.skip').addEventListener('click',event=>{event.preventDefault();root.focus();});
window.addEventListener('hashchange',show);
window.addEventListener('lab:unauthorized',()=>{signedOut();document.querySelector('#login-error').textContent='Your session has expired. Sign in to continue.';});
window.addEventListener('lab:error',event=>notice(event.detail,true));
signedIn().catch(()=>signedOut());
