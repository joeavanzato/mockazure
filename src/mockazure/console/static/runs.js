import {api,el,button,heading,table,download} from "./api.js";

export async function runs(root,ctx){
  let disposed=false,timer=null,selected=ctx.runId();
  const listing=el("div"),detail=el("section",{class:"panel"});
  async function show(id){selected=id;clearTimeout(timer);await poll();}
  async function poll(){
    if(!selected||disposed)return;
    const value=await api(`/_mock/population/runs/${selected}`),steps=await api(`/_mock/population/runs/${selected}/steps?limit=100`);
    if(disposed)return;
    const active=["pending","running","verifying"].includes(value.state);
    detail.replaceChildren(el("div",{class:"heading"},el("div",{},el("p",{class:"eyebrow"},"RUN / "+value.id),el("h2",{},value.state==="succeeded"?"Population complete":value.state==="needs-attention"?"Reconciliation needed":"Population progress")),el("span",{class:"badge "+value.state},value.state)),
      el("p",{class:"hint"},`Tenant ${value.tenant_id} · ${value.steps.completed} / ${value.steps.total} steps complete`),
      el("div",{class:"statline"},["created","unchanged","verified"].map(key=>el("div",{},el("span",{class:"metric"},value.progress[key]||0),el("small",{},key)))),
      ...value.errors.map(error=>el("p",{class:"error"},error)),
      el("div",{class:"actions"},button(active?"Cancel run":"Resume and recheck",async()=>{await api(`/_mock/population/runs/${selected}/${active?"cancel":"resume"}`,{method:"POST"});await poll();}),button("Download manifest",async()=>download(`run-${selected}.json`,await api(`/_mock/population/runs/${selected}/manifest`))),
        button("Download ground truth",async()=>{
          let after=null,lines=[],size=0;
          do{const page=await api(`/_mock/population/runs/${selected}/ground-truth`+(after?`?after=${encodeURIComponent(after)}`:""));const line=JSON.stringify(page)+"\n";size+=line.length;if(size>16*1024*1024)throw new Error("Use the population CLI to stream reports larger than 16 MiB.");lines.push(line);after=page.next_after;}while(after);
          download(`ground-truth-${selected}.jsonl`,lines.join(""),"application/x-ndjson");
        })),
      el("h3",{style:"margin-top:25px"},"Steps"),table(["ordinal","key","family","count","offset","state"],steps.items),
      steps.next_after!==null?el("p",{class:"hint"},"Showing the first 100 steps. The steps API supports explicit pagination."):null);
    if(active)timer=setTimeout(()=>poll().catch(ctx.error),1500);
  }
  async function list(){const page=await api("/_mock/population/runs?limit=50"+(ctx.tenant()?`&tenant_id=${ctx.tenant().id}`:""));if(disposed)return;listing.replaceChildren(page.items.length?table(["id","state","tenant_id"],page.items,row=>show(row.id)):el("div",{class:"empty"},el("strong",{},"No population runs yet"),"Preview a template in Profiles to get started."));}
  root.replaceChildren(heading("Population runs","Follow durable progress, inspect outcomes and resume safely after interruption.",[button("Refresh",async()=>{await list();await poll();})]),detail,listing);
  detail.hidden=!selected;await list();if(selected)await poll();
  // Open an item from the list even when the view began without a selected run.
  const observer=new MutationObserver(()=>{if(detail.childNodes.length)detail.hidden=false;});observer.observe(detail,{childList:true});
  return ()=>{disposed=true;clearTimeout(timer);observer.disconnect();};
}
