import {api,el,setCSRF} from "./api.js";
import {resources} from "./resources.js";
import {profiles} from "./profiles.js";
import {runs} from "./runs.js";
import {inspector} from "./inspector.js";
import {hunting} from "./hunting.js";

const views={resources,profiles,runs,inspector,hunting},root=document.querySelector("#workspace"),tenantSelect=document.querySelector("#tenant");
let tenants=[],after=null,current="resources",cleanup=null,runId=null,generation=0;
function notice(message,error=false){const box=document.querySelector("#notice");box.textContent=message;box.className=error?"error":"";box.hidden=false;}
const ctx={tenant:()=>tenants.find(t=>t.id===tenantSelect.value),notice,error:error=>notice(error.message||String(error),true),refreshTenants:()=>loadTenants(),runId:()=>runId,openRun:id=>{runId=id;show("runs");}};
async function loadTenants(more=false){
  const previous=tenantSelect.value,page=await api("/_mock/tenants?limit=100"+(more&&after?`&after=${after}`:""));
  tenants=more?[...tenants,...page.items]:page.items;after=page.next_after;
  tenantSelect.replaceChildren(el("option",{value:""},"Select a tenant"),...tenants.map(t=>el("option",{value:t.id},t.display_name)));
  tenantSelect.value=previous;document.querySelector("#more-tenants").hidden=!after;
}
async function show(name){
  const own=++generation;cleanup?.();cleanup=null;current=name;document.querySelector("#view-title").textContent=name[0].toUpperCase()+name.slice(1);
  document.querySelectorAll("[data-view]").forEach(b=>b.classList.toggle("active",b.dataset.view===name));
  const panel=el("div",{},el("p",{class:"muted"},"Loading workspace…"));root.replaceChildren(panel);
  try{const returned=await views[name](panel,ctx);if(own===generation)cleanup=returned;else returned?.();}catch(error){ctx.error(error);}
}
function signedOut(){cleanup?.();cleanup=null;++generation;setCSRF(null);document.querySelector("#lab").hidden=true;document.querySelector("#login").hidden=false;document.querySelector("#password").value="";}
async function signedIn(){const session=await api("/_mock/console/session");setCSRF(session.csrf_token);document.querySelector("#inspector-nav").hidden=!session.inspector_enabled;await loadTenants();document.querySelector("#login").hidden=true;document.querySelector("#lab").hidden=false;await show(current);}
document.querySelector("#login-form").addEventListener("submit",async event=>{event.preventDefault();const submit=event.currentTarget.querySelector("button");submit.disabled=true;document.querySelector("#login-error").textContent="";try{const result=await api("/_mock/console/login",{method:"POST",anonymous:true,json:{password:document.querySelector("#password").value}});setCSRF(result.csrf_token);document.querySelector("#password").value="";await signedIn();}catch(error){document.querySelector("#login-error").textContent=error.message;}finally{submit.disabled=false;}});
document.querySelectorAll("[data-view]").forEach(button=>button.addEventListener("click",()=>show(button.dataset.view)));
tenantSelect.addEventListener("change",()=>show(current));
document.querySelector("#more-tenants").addEventListener("click",()=>loadTenants(true).catch(ctx.error));
document.querySelector("#refresh-tenants").addEventListener("click",()=>loadTenants().catch(ctx.error));
document.querySelector("#sign-out").addEventListener("click",async()=>{try{await api("/_mock/console/logout",{method:"POST"});signedOut();}catch(error){ctx.error(error);}});
window.addEventListener("lab:unauthorized",signedOut);
window.addEventListener("lab:error",event=>notice(event.detail,true));
signedIn().catch(()=>signedOut());
