import {el,button,heading,table,download,jsonView,query} from './api.js';
import {empty,disclosure} from './components.js';
export async function runs(root,ctx){
 const selected=ctx.route.run,tenant=ctx.tenant()?.id;let disposed=false,timer=null,polling=false,commandPending=false;
 const configPanel=disclosure('Immutable run configuration',el('p',{class:'hint'},'Public configuration from this run’s persisted manifest. Protected inputs remain excluded.'),button('Load configuration',async event=>{const control=event.currentTarget;control.disabled=true;try{const manifest=await ctx.api(path('/manifest'));if(ctx.active())control.after(jsonView(manifest));}catch(error){control.disabled=false;throw error;}}));
 const listing=el('div'),detail=el('section',{class:'panel'}),historyPaging=el('div',{class:'paging'});let after=null;
 const suffix=tenant?'?tenant_id='+encodeURIComponent(tenant):'';
 const path=kind=>`/_mock/population/runs/${encodeURIComponent(selected)}${kind||''}${suffix}`;
 async function report(name){const value=await ctx.api(path('/'+name));if(ctx.active())download(`run-${selected}-${name}.json`,value);}
 async function groundTruth(){let cursor=null,lines=[],bytes=0;do{const page=await ctx.api(`/_mock/population/runs/${selected}/ground-truth?`+query({tenant_id:tenant,after:cursor}));const line=JSON.stringify(page)+'\n';bytes+=new TextEncoder().encode(line).length;if(bytes>16777216)throw new Error('Use the population CLI to stream ground truth larger than 16 MiB.');lines.push(line);cursor=page.next_after;}while(cursor&&ctx.active());if(ctx.active())download(`run-${selected}-ground-truth.jsonl`,lines.join(''),'application/x-ndjson');}
 async function command(action){if(commandPending)return;commandPending=true;ctx.notice(action==='cancel'?'Cancellation requested; waiting for server acknowledgement. Completed work may remain.':'Requesting resume and recheck of persisted work…');try{await ctx.api(path('/'+action),{method:'POST'});if(ctx.active()){ctx.notice(action==='cancel'?'Cancellation request acknowledged. The status below reflects the worker’s durable state; completed work is retained.':'Resume requested. The worker will recheck recorded results.');await poll();}}finally{commandPending=false;}}
 async function poll(){
  if(!selected||disposed||polling)return;clearTimeout(timer);polling=true;
  try{const [value,steps]=await Promise.all([ctx.api(path()),ctx.api(`/_mock/population/runs/${selected}/steps?`+query({tenant_id:tenant,limit:100}))]);if(!ctx.active()||disposed)return;
   if(tenant&&value.tenant_id!==tenant)throw new Error('This run belongs to another tenant. Open it from its tenant history.');
   const active=['pending','running','verifying'].includes(value.state);
   const progress=el('progress',{max:Math.max(value.steps.total,1),value:value.steps.completed,'aria-label':'Completed population steps'});
   const controls=el('div',{class:'actions'},button(active?'Cancel run':'Resume and recheck',()=>command(active?'cancel':'resume')),button('Download manifest',()=>report('manifest')),button('Download ground truth',groundTruth));
   const focused=detail.contains(document.activeElement)?document.activeElement.textContent:null;
   detail.replaceChildren(el('div',{class:'split-heading'},el('div',{},el('p',{class:'eyebrow'},'POPULATION RUN'),el('h2',{},value.state==='succeeded'?'Population complete':value.state==='needs-attention'?'Reconciliation needed':'Population progress')),el('span',{class:'badge '+value.state},value.state)),
    el('p',{class:'mono'},value.id),el('p',{class:'hint'},`Tenant ${value.tenant_id} · ${value.steps.completed} / ${value.steps.total} steps complete`),progress,
    value.activity?el('p',{class:'hint activity-progress'},`Activity ${value.activity.recipe_step}: ${value.activity.completed} / ${value.activity.total} actions verified · ${value.activity.pacing_ms} ms pacing · Evidence reference ${value.activity.reference_time}`):null,
    el('div',{class:'statline'},['created','unchanged','verified'].map(k=>el('div',{},el('span',{class:'metric'},value.progress[k]||0),el('small',{},k)))),...value.errors.map(e=>el('p',{class:'error',role:'alert'},e)),controls,
    el('p',{class:'hint'},'Cancellation is not rollback. Resume rechecks persisted work under the server’s recovery rules.'),
    el('h3',{},'Steps'),table(['ordinal','key','family','count','offset','state'],steps.items),steps.next_after!==null?el('p',{class:'hint'},'Showing the first 100 steps. Use the steps API for further pages.'):null,
    configPanel);
   if(focused){const next=[...detail.querySelectorAll('button')].find(b=>b.textContent===focused);next?.focus({preventScroll:true});}
   if(active)timer=setTimeout(()=>poll().catch(ctx.error),1500);
  }catch(error){if(ctx.active()&&error.name!=='AbortError'){detail.replaceChildren(empty('Run unavailable',error.message),button('Retry',poll));timer=setTimeout(()=>poll().catch(ctx.error),5000);}}finally{polling=false;}
 }
 async function list(next=false){const page=await ctx.api('/_mock/population/runs?'+query({limit:50,tenant_id:tenant,after:next?after:null}));if(!ctx.active())return;after=page.next_after;listing.replaceChildren(page.items.length?table(['id','state','tenant_id'],page.items,row=>ctx.openRun(row.id,row.tenant_id)):empty('No population runs yet','Create a preview in Compose lab to get started.'));const more=button('Next runs',()=>list(true));more.disabled=!after;historyPaging.replaceChildren(button('First runs',()=>list()),more);}
 root.replaceChildren(heading('Follow every step.','Durable progress, verified results, and recovery when a run needs attention.',[button('Refresh',async()=>{await list();await poll();})]),detail,el('div',{class:'split-heading'},el('h2',{},'Run history'),el('span',{class:'badge'},tenant?'Selected tenant':'All tenants')),listing,historyPaging);
 detail.hidden=!selected;await list();if(selected)await poll();return ()=>{disposed=true;clearTimeout(timer);};
}
