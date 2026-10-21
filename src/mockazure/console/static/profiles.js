import {el,field,button,jsonView,query,heading,download,table,objectInput} from "./api.js";
import {Revision} from "./state.js";
import {friendly,disclosure} from "./components.js";

export async function profiles(root,ctx){
  let bundle=await ctx.api("/_mock/profiles/builtin"),dirty=false,preview=null;
  const revision=new Revision(); let updateSummary=()=>{}, sourceDirty=false, step=0;
  const savedBundles=el("select",{"aria-label":"Saved bundle"});
  let nextBundlePage=null;
  const nextBundles=button("Next bundles",()=>listBundles(nextBundlePage),"small");
  async function listBundles(after=null){
    const page=await ctx.api("/_mock/profiles?"+query({limit:50,after}));
    savedBundles.replaceChildren(el("option",{value:"builtin"},"Built-in templates"),...page.items.map(item=>el("option",{value:item.id},`${item.id} · revision ${item.revision}`)));
    nextBundlePage=page.next_after;nextBundles.disabled=!nextBundlePage;
  }
  function selectSaved(){
    if(![...savedBundles.options].some(option=>option.value===bundle.id))savedBundles.append(el("option",{value:bundle.id},`${bundle.id} · revision ${bundle.revision}`));
    savedBundles.value=bundle.id;
  }
  const loadBundle=button("Load bundle",async()=>{
    invalidate();
    const ticket=revision.capture();const loaded=await ctx.api("/_mock/profiles/"+encodeURIComponent(savedBundles.value));if(!ctx.active()||!revision.current(ticket))return;
    bundle=loaded;dirty=false;sourceDirty=false;populateSelectors("developer-lab");
    saveStatus.textContent=`Loaded revision ${bundle.revision} · ${bundle.id}`;
  });
  await listBundles();
  const templates=el("select",{"aria-label":"Template"}),tenantProfile=el("select",{"aria-label":"Tenant profile"});
  const layerSelect=el("select",{"aria-label":"Add data layer"}),layers=el("ol",{class:"layer-list"});
  const namespace=el("input",{value:"my-lab",pattern:"[A-Za-z0-9._-]+"}),users=el("input",{type:"number",min:1,max:100000,value:20});
  const days=el("input",{type:"number",min:1,max:3660,value:7}),seed=el("input",{type:"number",value:0});
  const activity=el("input",{type:"checkbox"}),activityStep=el("input",{value:"calendar",placeholder:"Public action step key"});
  const actionCount=el("input",{type:"number",min:1,max:100000,value:10}),pacing=el("input",{type:"number",min:0,max:60000,value:0});
  const activityFields=el("div",{class:"form-grid",hidden:true},field("Activity recipe step",activityStep),field("Action count",actionCount),field("Pacing (milliseconds)",pacing));
  activity.addEventListener("change",()=>{activityFields.hidden=!activity.checked;invalidate();});
  const extra=el("textarea",{"aria-label":"Additional parameters JSON",value:"{}",rows:3,style:"min-height:90px"});
  const create=el("input",{type:"checkbox",checked:!ctx.tenant()}),slug=el("input",{value:"my-lab"}),domain=el("input",{value:"my-lab.test"}),display=el("input",{value:"My lab"});
  const target=el("div",{class:"form-grid"},field("Tenant slug",slug),field("Tenant domain",domain),field("Display name",display),el("p",{class:"hint"},"New starter tenants use the E5 lab licence label. You can change tenant settings after creation."));
  const editor=el("textarea",{"aria-label":"Bundle JSON",spellcheck:false}),result=el("div"),saveStatus=el("p",{class:"hint"});
  const apply=button("Apply this preview",async()=>{
    if(!preview)return;const reviewed=preview;preview=null;apply.disabled=true;
    const run=await ctx.api("/_mock/population/runs",{method:"POST",json:{preview_id:reviewed.id,digest:reviewed.digest}});
    ctx.notice("Population run started. Completed batches remain available if the run is interrupted.");await ctx.refreshTenants();ctx.openRun(run.id,run.tenant_id);
  },"primary");apply.disabled=true;
  function invalidate(){revision.invalidate();preview=null;apply.disabled=true;result.replaceChildren();updateSummary();}
  function effective(name){let merged={},seen=new Set();const chain=[];while(name){if(seen.has(name))throw new Error("Template inheritance cycle");seen.add(name);const item=bundle.documents[name];if(!item)throw new Error("Missing template parent");chain.unshift(item);name=item.extends;}for(const item of chain)merged={...merged,...item,parameters:{...merged.parameters,...item.parameters}};return merged;}
  function syncEditor(){editor.value=JSON.stringify({documents:bundle.documents},null,2);dirty=true;invalidate();}
  function writeComposition(nextLayers){
    const current=effective(templates.value);
    if(sourceDirty)throw new Error("Load editor changes before changing layers; your source has been preserved.");
    bundle.documents[templates.value]={...bundle.documents[templates.value],schemaVersion:1,kind:"template",name:templates.value,tenantProfile:tenantProfile.value,dataProfiles:nextLayers,parameters:current.parameters||{}};
    syncEditor();renderLayers();
  }
  function renderLayers(){
    const names=[...(effective(templates.value).dataProfiles||[])];
    layers.replaceChildren(...names.map((name,index)=>el("li",{class:"layer"},el("div",{},el("span",{class:"badge"},String(index+1).padStart(2,"0"))," ",el("span",{},friendly(name)),el("code",{class:"layer-key"},name)),el("div",{class:"actions"},
      button("↑",()=>{if(index>0){[names[index-1],names[index]]=[names[index],names[index-1]];writeComposition(names);}},"small"),
      button("↓",()=>{if(index<names.length-1){[names[index+1],names[index]]=[names[index],names[index+1]];writeComposition(names);}},"small"),
      button("Remove",()=>writeComposition(names.filter((_,i)=>i!==index)),"small")))));
    [...layers.children].forEach((row,index)=>{const controls=row.querySelectorAll("button");controls[0].disabled=index===0;controls[1].disabled=index===names.length-1;controls[0].setAttribute("aria-label","Move "+names[index]+" up");controls[1].setAttribute("aria-label","Move "+names[index]+" down");});
    if(!names.length)layers.append(el("p",{class:"hint"},"No data layers selected. Tenant setup still runs."));
  }
  function selectTemplate(){
    const composed=effective(templates.value);tenantProfile.value=composed.tenantProfile;
    users.value=composed.parameters?.["shared.users"]||20;
    days.value=composed.parameters?.["shared.days"]??7;
    seed.value=composed.parameters?.["shared.seed"]??0;
    extra.value=JSON.stringify(Object.fromEntries(Object.entries(composed.parameters||{}).filter(([k])=>!["shared.users","shared.days","shared.seed"].includes(k))),null,2);
    const defaults={datasetNamespace:"my-lab",createTenant:!ctx.tenant(),tenantSlug:"my-lab",tenantDomain:"my-lab.test",displayName:"My lab",activityEnabled:false,activityStep:"calendar",actionCount:10,pacingMs:0,...composed.consoleDefaults};
    if(defaults){namespace.value=defaults.datasetNamespace;create.checked=defaults.createTenant;slug.value=defaults.tenantSlug;domain.value=defaults.tenantDomain;display.value=defaults.displayName;activity.checked=defaults.activityEnabled;activityStep.value=defaults.activityStep;actionCount.value=defaults.actionCount;pacing.value=defaults.pacingMs;}
    target.hidden=!create.checked;activityFields.hidden=!activity.checked;
    renderLayers();invalidate();
  }
  function populateSelectors(preferred){
    const values=Object.values(bundle.documents);
    templates.replaceChildren(...values.filter(d=>d.kind==="template").map(d=>el("option",{value:d.name},friendly(d.name))));
    tenantProfile.replaceChildren(...values.filter(d=>d.kind==="tenant").map(d=>el("option",{value:d.name},friendly(d.name))));
    layerSelect.replaceChildren(...values.filter(d=>d.kind==="data").map(d=>el("option",{value:d.name},friendly(d.name))));
    if(preferred && [...templates.options].some(o=>o.value===preferred))templates.value=preferred;
    selectTemplate();editor.value=JSON.stringify({documents:bundle.documents},null,2);
  }
  async function save(){
    if(!dirty)return bundle.id;
    const source=editor.value,value=objectInput(editor);
    if(!value.documents)throw new Error("Bundle JSON must contain documents.");
    const saved=bundle.id==="builtin"?await ctx.api("/_mock/profiles",{method:"POST",json:value}):await ctx.api(`/_mock/profiles/${bundle.id}?expected_revision=${bundle.revision}`,{method:"PUT",json:value});
    bundle={...bundle,...saved};dirty=editor.value!==source;if(!dirty)bundle.documents=value.documents;selectSaved();saveStatus.textContent=`Saved revision ${saved.revision} · ${saved.id}`;return saved.id;
  }
  const previewButton=button("Preview population",async()=>{
    if(sourceDirty)throw new Error("Load editor changes before previewing the composition.");
    syncParameters();const ticket=revision.capture();
    previewButton.disabled=true;
    try{
      const bundleId=await save();if(!ctx.active()||!revision.current(ticket))return;
      if(!create.checked&&!ctx.tenant())throw new Error("Select a tenant above, or choose Create a new tenant.");
      const prepared=await ctx.api("/_mock/population/previews",{method:"POST",json:{bundle_id:bundleId,template:templates.value,dataset_namespace:namespace.value,
        target:create.checked?{create_tenant:{slug:slug.value,domain:domain.value,display_name:display.value,licences:["E5"]}}:{existing_tenant_id:ctx.tenant().id},
        parameters:{"shared.users":Number(users.value),"shared.days":Number(days.value),"shared.seed":Number(seed.value),...objectInput(extra)},
        activity:activity.checked?{recipe_step:activityStep.value,action_count:Number(actionCount.value),pacing_ms:Number(pacing.value)}:null}});
      if(!ctx.active()||!revision.current(ticket))return;preview=prepared;
      result.replaceChildren(el("h2",{},"Review the resolved plan"),el("p",{class:"hint"},`Target: ${preview.tenant_id} · Expires ${new Date(preview.expires_at).toLocaleString()}`),
        table(["family","count"],Object.entries(preview.counts).map(([family,count])=>({family,count}))),
        ...preview.warnings.map(w=>el("p",{class:"hint"},w)),el("details",{},el("summary",{},"Inspect bounded samples and digests"),jsonView(preview)));
      apply.disabled=false;updateSummary();
    }finally{previewButton.disabled=false;}
  },"primary");
  const importFile=el("input",{type:"file",multiple:true,accept:".json,.yaml,.yml"});
  const importButton=button("Import files",async()=>{
    if(!importFile.files.length)throw new Error("Choose JSON or YAML profile files.");
    invalidate();const ticket=revision.capture();
    const documents={};let bytes=0;
    for(const file of importFile.files){bytes+=file.size;if(bytes>16*1024*1024)throw new Error("Bundle exceeds 16 MiB.");const source=await file.text();let parsed;try{parsed=JSON.parse(source);}catch{}if(parsed?.documents){if(Object.keys(parsed).some(k=>k!=="documents"))throw new Error("Bundle imports support a documents object only; unknown fields were not discarded.");for(const [key,value]of Object.entries(parsed.documents)){if(key in documents)throw new Error("Duplicate document across imported files: "+key);documents[key]=value;}}else {if(file.name in documents)throw new Error("Duplicate imported file name.");documents[file.name]=source;}}
    const saved=await ctx.api("/_mock/profiles",{method:"POST",json:{documents}});const loaded=await ctx.api(`/_mock/profiles/${saved.id}`);if(!ctx.active()||!revision.current(ticket))return;bundle=loaded;dirty=false;sourceDirty=false;selectSaved();populateSelectors();saveStatus.textContent=`Imported revision ${saved.revision}`;
  });
  create.addEventListener("change",()=>{target.hidden=!create.checked;invalidate();});target.hidden=!create.checked;
  templates.addEventListener("change",selectTemplate);
  tenantProfile.addEventListener("change",()=>writeComposition(effective(templates.value).dataProfiles||[]));
  [namespace,users,days,seed,extra,slug,domain,display,activityStep,actionCount,pacing].forEach(node=>node.addEventListener("input",invalidate));
  editor.addEventListener("input",()=>{dirty=true;sourceDirty=true;invalidate();});
  function syncParameters(){
    if(sourceDirty)throw new Error("Load editor changes before saving configuration fields.");
    for(const input of [users,days,seed,actionCount,pacing])if(!input.checkValidity()){input.reportValidity();throw new Error("Correct the highlighted number.");}
    const current=bundle.documents[templates.value];
    bundle.documents[templates.value]={...current,parameters:{...objectInput(extra),"shared.users":Number(users.value),"shared.days":Number(days.value),"shared.seed":Number(seed.value)},consoleDefaults:{datasetNamespace:namespace.value,createTenant:create.checked,tenantSlug:slug.value,tenantDomain:domain.value,displayName:display.value,activityEnabled:activity.checked,activityStep:activityStep.value,actionCount:Number(actionCount.value),pacingMs:Number(pacing.value)}};
    editor.value=JSON.stringify({documents:bundle.documents},null,2);dirty=true;
  }
  const summary=el("aside",{class:"panel context-card"});
  updateSummary=()=>summary.replaceChildren(el("p",{class:"eyebrow"},"YOUR COMPOSITION"),el("h2",{},"A connected starting point"),el("dl",{class:"summary-list"},
    [["Tenant profile",tenantProfile.value],["Data layers",(effective(templates.value).dataProfiles||[]).join(" → ")],["Target",create.checked?display.value:ctx.tenant()?.display_name||"Select a tenant"],["Namespace",namespace.value],["Scale",users.value+" users · "+days.value+" days"],["Seed",seed.value],["Activity",activity.checked?actionCount.value+" actions · "+pacing.value+" ms":"No finite batch"]].map(([key,value])=>[el("dt",{},key),el("dd",{},value)])),el("p",{class:"hint"},preview?"Counts below are resolved by the server.":"Resource counts are available after server preview."));
  const panels=[
    el("section",{class:"panel"},el("h2",{},"Choose your layers"),el("p",{class:"hint"},"Start with a tenant profile, then stack reusable data. Order is preserved."),el("div",{class:"form-grid"},field("Template",templates),field("Tenant profile",tenantProfile)),layers,
      el("div",{class:"toolbar"},field("Add data layer",layerSelect),button("Add layer",()=>{const names=[...(effective(templates.value).dataProfiles||[])];if(!names.includes(layerSelect.value))writeComposition([...names,layerSelect.value]);})),button("Continue to configure",()=>setStep(1),"primary")),
    el("section",{class:"panel"},el("h2",{},"Configure the population"),field("Create a new tenant",create,"check"),target,
      el("div",{class:"form-grid"},field("Dataset namespace",namespace),field("Users",users),field("Days of activity",days),field("Seed",seed)),field("Additional parameters JSON",extra),
      ctx.capabilities.finite_activity?field("Run a finite activity batch",activity,"check"):null,activityFields,
      el("p",{class:"hint"},"Activity repeats one supported public action after setup, with a fixed count. A reused namespace rechecks recorded results; use a new namespace for an independent batch."),button("Continue to review",()=>setStep(2),"primary")),
    el("section",{class:"panel"},el("h2",{},"Review before populating"),el("p",{class:"hint"},"Resolve the exact target, ordered layers and parameters on the server. No data is created during preview."),el("div",{class:"actions"},previewButton,apply),result)
  ];
  const stepper=el("nav",{class:"stepper","aria-label":"Composition steps"});
  function setStep(next){step=next;panels.forEach((p,i)=>p.hidden=i!==step);[...stepper.children].forEach((b,i)=>{if(i===step)b.setAttribute("aria-current","step");else b.removeAttribute("aria-current");});}
  ["Choose layers","Configure","Review"].forEach((label,i)=>stepper.append(button(`${i+1}. ${label}`,()=>setStep(i))));
  const source=disclosure("Profile source, import & export",el("p",{class:"hint"},"Structured JSON/YAML only. Loading a saved bundle replaces this draft; export it first to keep a copy."),field("Profile files",importFile),el("div",{class:"actions"},importButton,button("Export bundle",()=>{if(!sourceDirty)syncParameters();const value=objectInput(editor);download("mockazure-profiles.json",value);})),field("Bundle JSON",editor),el("div",{class:"actions"},button("Load editor changes",()=>{const value=objectInput(editor);if(Object.keys(value).length!==1||!value.documents||Array.isArray(value.documents)||typeof value.documents!=="object")throw new Error("Expected a documents object only.");const docs=Object.values(value.documents);if(!docs.some(d=>d?.kind==="template")||docs.some(d=>!d||typeof d!=="object"||!["tenant","data","template"].includes(d.kind)||!d.name))throw new Error("Provide named profile documents and at least one template.");const previous=bundle.documents,raw=editor.value;try{bundle.documents=value.documents;sourceDirty=false;populateSelectors(templates.value);dirty=true;invalidate();}catch(error){bundle.documents=previous;populateSelectors();editor.value=raw;sourceDirty=true;dirty=true;throw error;}}),button("Save revision",async()=>{if(!sourceDirty)syncParameters();await save();})));
  root.replaceChildren(heading("Build a useful starting point.","Compose a tenant from reusable layers, then review exactly what will be populated."),
    el("section",{class:"panel"},el("div",{class:"toolbar"},field("Saved bundle",savedBundles),loadBundle,button("First bundles",()=>listBundles(),"small"),nextBundles),saveStatus),
    stepper,el("div",{class:"two-col"},el("div",{},panels,source),summary));
  populateSelectors("developer-lab");setStep(0);updateSummary();
  return ()=>revision.invalidate();
}
