import {api,el,field,button,jsonView,query,heading,download,table} from "./api.js";

export async function profiles(root,ctx){
  let bundle=await api("/_mock/profiles/builtin"),dirty=false,preview=null;
  const savedBundles=el("select",{"aria-label":"Saved bundle"});
  let nextBundlePage=null;
  const nextBundles=button("Next bundles",()=>listBundles(nextBundlePage),"small");
  async function listBundles(after=null){
    const page=await api("/_mock/profiles?"+query({limit:50,after}));
    savedBundles.replaceChildren(el("option",{value:"builtin"},"Built-in templates"),...page.items.map(item=>el("option",{value:item.id},`${item.id} · revision ${item.revision}`)));
    nextBundlePage=page.next_after;nextBundles.disabled=!nextBundlePage;
  }
  function selectSaved(){
    if(![...savedBundles.options].some(option=>option.value===bundle.id))savedBundles.append(el("option",{value:bundle.id},`${bundle.id} · revision ${bundle.revision}`));
    savedBundles.value=bundle.id;
  }
  const loadBundle=button("Load bundle",async()=>{
    invalidate();
    const loaded=await api("/_mock/profiles/"+encodeURIComponent(savedBundles.value));
    bundle=loaded;dirty=false;populateSelectors("developer-lab");
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
    if(!preview)return;apply.disabled=true;
    const run=await api("/_mock/population/runs",{method:"POST",json:{preview_id:preview.id,digest:preview.digest}});
    ctx.notice("Population run started. Completed batches remain available if the run is interrupted.");await ctx.refreshTenants();ctx.openRun(run.id);
  },"primary");apply.disabled=true;
  function invalidate(){preview=null;apply.disabled=true;result.replaceChildren();}
  function effective(name){let merged={},seen=new Set();const chain=[];while(name){if(seen.has(name))throw new Error("Template inheritance cycle");seen.add(name);const item=bundle.documents[name];if(!item)throw new Error("Missing template parent");chain.unshift(item);name=item.extends;}for(const item of chain)merged={...merged,...item,parameters:{...merged.parameters,...item.parameters}};return merged;}
  function syncEditor(){editor.value=JSON.stringify({documents:bundle.documents},null,2);dirty=true;invalidate();}
  function writeComposition(nextLayers){
    const current=effective(templates.value);
    bundle.documents[templates.value]={schemaVersion:1,kind:"template",name:templates.value,tenantProfile:tenantProfile.value,dataProfiles:nextLayers,parameters:current.parameters||{}};
    syncEditor();renderLayers();
  }
  function renderLayers(){
    const names=effective(templates.value).dataProfiles||[];
    layers.replaceChildren(...names.map((name,index)=>el("li",{class:"layer"},el("div",{},el("span",{class:"badge"},String(index+1).padStart(2,"0"))," ",el("code",{},name)),el("div",{class:"actions"},
      button("↑",()=>{if(index>0){[names[index-1],names[index]]=[names[index],names[index-1]];writeComposition(names);}},"small"),
      button("↓",()=>{if(index<names.length-1){[names[index+1],names[index]]=[names[index],names[index+1]];writeComposition(names);}},"small"),
      button("Remove",()=>writeComposition(names.filter((_,i)=>i!==index)),"small")))));
    if(!names.length)layers.append(el("p",{class:"hint"},"No data layers selected. Tenant setup still runs."));
  }
  function selectTemplate(){
    const composed=effective(templates.value);tenantProfile.value=composed.tenantProfile;
    users.value=composed.parameters?.["shared.users"]||20;
    days.value=composed.parameters?.["shared.days"]||7;
    renderLayers();invalidate();
  }
  function populateSelectors(preferred){
    const values=Object.values(bundle.documents);
    templates.replaceChildren(...values.filter(d=>d.kind==="template").map(d=>el("option",{value:d.name},d.name)));
    tenantProfile.replaceChildren(...values.filter(d=>d.kind==="tenant").map(d=>el("option",{value:d.name},d.name)));
    layerSelect.replaceChildren(...values.filter(d=>d.kind==="data").map(d=>el("option",{value:d.name},d.name)));
    if(preferred && [...templates.options].some(o=>o.value===preferred))templates.value=preferred;
    selectTemplate();editor.value=JSON.stringify({documents:bundle.documents},null,2);
  }
  async function save(){
    if(!dirty)return bundle.id;
    const value=JSON.parse(editor.value);
    if(!value.documents)throw new Error("Bundle JSON must contain documents.");
    const saved=bundle.id==="builtin"?await api("/_mock/profiles",{method:"POST",json:value}):await api(`/_mock/profiles/${bundle.id}?expected_revision=${bundle.revision}`,{method:"PUT",json:value});
    bundle={...saved,documents:value.documents};dirty=false;selectSaved();saveStatus.textContent=`Saved revision ${saved.revision} · ${saved.id}`;return saved.id;
  }
  const previewButton=button("Preview population",async()=>{
    previewButton.disabled=true;
    try{
      const bundleId=await save();
      if(!create.checked&&!ctx.tenant())throw new Error("Select a tenant above, or choose Create a new tenant.");
      preview=await api("/_mock/population/previews",{method:"POST",json:{bundle_id:bundleId,template:templates.value,dataset_namespace:namespace.value,
        target:create.checked?{create_tenant:{slug:slug.value,domain:domain.value,display_name:display.value,licences:["E5"]}}:{existing_tenant_id:ctx.tenant().id},
        parameters:{"shared.users":Number(users.value),"shared.days":Number(days.value),"shared.seed":Number(seed.value),...JSON.parse(extra.value)},
        activity:activity.checked?{recipe_step:activityStep.value,action_count:Number(actionCount.value),pacing_ms:Number(pacing.value)}:null}});
      result.replaceChildren(el("h2",{},"Review the resolved plan"),el("p",{class:"hint"},`Target: ${preview.tenant_id} · Expires ${new Date(preview.expires_at).toLocaleString()}`),
        table(["family","count"],Object.entries(preview.counts).map(([family,count])=>({family,count}))),
        ...preview.warnings.map(w=>el("p",{class:"hint"},w)),el("details",{},el("summary",{},"Inspect bounded samples and digests"),jsonView(preview)));
      apply.disabled=false;result.scrollIntoView({behavior:"smooth",block:"start"});
    }finally{previewButton.disabled=false;}
  },"primary");
  const importFile=el("input",{type:"file",multiple:true,accept:".json,.yaml,.yml"});
  const importButton=button("Import files",async()=>{
    if(!importFile.files.length)throw new Error("Choose JSON or YAML profile files.");
    const documents={};let bytes=0;
    for(const file of importFile.files){bytes+=file.size;if(bytes>16*1024*1024)throw new Error("Bundle exceeds 16 MiB.");const source=await file.text();let parsed;try{parsed=JSON.parse(source);}catch{}if(parsed?.documents)Object.assign(documents,parsed.documents);else documents[file.name]=source;}
    const saved=await api("/_mock/profiles",{method:"POST",json:{documents}});bundle=await api(`/_mock/profiles/${saved.id}`);dirty=false;selectSaved();populateSelectors();saveStatus.textContent=`Imported revision ${saved.revision}`;
  });
  create.addEventListener("change",()=>{target.hidden=!create.checked;invalidate();});target.hidden=!create.checked;
  templates.addEventListener("change",selectTemplate);
  tenantProfile.addEventListener("change",()=>writeComposition(effective(templates.value).dataProfiles||[]));
  [namespace,users,days,seed,extra,slug,domain,display,activityStep,actionCount,pacing].forEach(node=>node.addEventListener("input",invalidate));
  editor.addEventListener("input",()=>{dirty=true;invalidate();});
  root.replaceChildren(heading("Compose a populated lab", "Stack reusable data layers on a tenant profile. Preview resolves the complete plan before any resources are created."),
    el("section",{class:"panel"},el("div",{class:"toolbar"},field("Saved bundle",savedBundles),loadBundle,button("First bundles",()=>listBundles(),"small"),nextBundles),el("p",{class:"hint"},"Browse 50 saved bundles at a time. Loading replaces unsaved editor changes."),saveStatus),
    el("div",{class:"columns"},el("section",{class:"panel"},el("h2",{},"01 / Composition"),el("div",{class:"form-grid"},field("Template",templates),field("Tenant profile",tenantProfile)),layers,
      el("div",{class:"toolbar"},field("Add data layer",layerSelect),button("Add layer",()=>{const names=effective(templates.value).dataProfiles||[];if(!names.includes(layerSelect.value))writeComposition([...names,layerSelect.value]);}))),
      el("section",{class:"panel"},el("h2",{},"02 / Target & parameters"),field("Create a new tenant",create,"check"),target,
        el("div",{class:"form-grid"},field("Dataset namespace",namespace),field("Users",users),field("Days of activity",days),field("Seed",seed)),field("Additional parameters JSON",extra),field("Run a finite activity batch",activity,"check"),activityFields,el("p",{class:"hint"},"Activity repeats one supported public action after its setup, with a fixed count. Other steps populate their declared data. Reusing a namespace rechecks recorded results; use a new namespace for another batch."))),
    el("details",{class:"panel"},el("summary",{},"Profile source, import & export"),el("p",{class:"hint"},"Structured data only. Imported text cannot run code or read server files."),field("Profile files",importFile),el("div",{class:"actions"},importButton,button("Export bundle",()=>download("mockazure-profiles.json",{documents:bundle.documents}))),field("Bundle JSON",editor),el("div",{class:"actions"},button("Load editor changes",()=>{const value=JSON.parse(editor.value);if(!value.documents)throw new Error("Expected documents.");bundle.documents=value.documents;populateSelectors(templates.value);dirty=true;invalidate();}),button("Save revision",save))),
    el("div",{class:"actions"},previewButton,apply),el("section",{class:"panel",style:"margin-top:24px"},result));
  populateSelectors("developer-lab");
}
