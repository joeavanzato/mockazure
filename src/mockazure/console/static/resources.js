import {api,el,field,button,table,jsonView,query,heading,dialog} from "./api.js";
let selected="directory.users";

export async function resources(root,ctx) {
  const catalog=(await api("/_mock/resources/catalog")).families;
  const family=el("select",{"aria-label":"Resource family"});
  for (const service of [...new Set(catalog.map(f=>f.service))].sort()) {
    const group=el("optgroup",{label:service});
    catalog.filter(f=>f.service===service).forEach(f=>group.append(el("option",{value:f.key},f.key)));
    family.append(group);
  }
  family.value=selected;
  const parent=el("input",{placeholder:"Optional parent / workload scope"}),filters=el("input",{value:"{}"});
  const metadata=el("div"),rows=el("div"),paging=el("div",{class:"paging"});
  let continuation=null, disposed=false;
  const descriptor=()=>catalog.find(f=>f.key===family.value);
  const add=button("Add data",()=>edit(descriptor(),ctx,load),"primary");
  async function load(next=false) {
    if (!ctx.tenant()) { rows.replaceChildren(el("div",{class:"empty"},el("strong",{},"Choose a tenant to explore"),"Create a populated tenant from Profiles, or select one above.")); add.disabled=true;return; }
    const f=descriptor();selected=f.key;add.disabled=!f.operations.length;
    metadata.replaceChildren(el("div",{class:"api-path"},el("span",{class:"badge"},f.service),el("code",{},f.api_path)),
      el("details",{},el("summary",{},"Supported operations & limits"),el("p",{class:"hint"},f.operations.length?f.operations.map(o=>`${o.key} · ${o.mode}`).join(" / "):"Browsing only. Use the documented public API or import for changes."),...f.limitations.map(note=>el("p",{class:"hint"},note))));
    rows.replaceChildren(el("p",{class:"muted"},"Loading this page…"));
    const page=await api(`/_mock/tenants/${ctx.tenant().id}/resources/${f.key}?`+query({limit:50,parent:parent.value,filters:filters.value,continuation:next?continuation:null}));
    if(disposed)return;
    continuation=page.continuation;
    const data=page.items.map(item=>({id:item.id,...item.properties,_resource:item}));
    const columns=[...new Set(["id",...f.display_fields])].slice(0,6);
    rows.replaceChildren(page.items.length?table(columns,data,row=>detail(f,ctx,row._resource,load,catalog)):el("div",{class:"empty"},el("strong",{},"No records in this view"),"Try another scope or populate this service using a profile."));
    paging.replaceChildren(el("span",{},`${page.items.length} records on this page · bounded to 50`),el("div",{class:"actions"},button("First page",()=>load()),button("Next page",()=>load(true))));
    paging.lastChild.lastChild.disabled=!continuation;
  }
  family.addEventListener("change",()=>{parent.value="";filters.value="{}";load().catch(ctx.error);});
  root.replaceChildren(heading("Explore your cloud", "Browse service data, inspect its API shape, and add records through validated operations.", [button("Upload content",()=>upload(ctx,load)),add]),
    el("div",{class:"toolbar"},field("Resource family",family),field("Scope / parent",parent),field("Filters (JSON)",filters,"narrow"),button("Load",()=>load())),metadata,rows,paging);
  await load();
  return ()=>{disposed=true;};
}

async function detail(f,ctx,item,refresh,catalog) {
  const page=await api(`/_mock/tenants/${ctx.tenant().id}/resources/${f.key}?`+query({identifier:item.id,parent:item.parent}));
  if(!page.items.length)throw new Error("This resource no longer exists. Refresh the page.");
  const full=page.items[0];
  const children=[el("p",{class:"hint"},f.api_path),jsonView(full),el("div",{class:"actions"},button("Prepare an edit",()=>edit(f,ctx,refresh,full)))];
  if(!f.operations.length)children[2].firstChild.disabled=true;
  if(f.key==="content.objects") children.push(el("a",{href:`/_mock/tenants/${ctx.tenant().id}/content/objects/${encodeURIComponent(full.id)}/$value`,download:`${full.id}.bin`},"Download content"));
  if(f.relationships.length)children.push(el("p",{class:"hint"},"Relationships: "+f.relationships.join(", ")));
  const links=await api(`/_mock/tenants/${ctx.tenant().id}/resources/${f.key}/related?`+query({identifier:full.id,parent:full.parent}));
  if(links.items.length)children.push(el("h3",{},"Related resources"),table(["relation","direction","family","id"],links.items,row=>{
    const target=catalog.find(entry=>entry.key===row.family);
    return detail(target,ctx,row,refresh,catalog);
  }));
  if(links.truncated)children.push(el("p",{class:"hint"},"Showing the first 50 relationships. Use the service API to collect larger relationships."));
  dialog(f.key,children);
}

function defaultPayload(f,ctx) {
  const id=crypto.randomUUID(),stamp=new Date().toISOString();
  if(f.key==="directory.users")return {id,displayName:"Lab user",userPrincipalName:`lab-${id.slice(0,8)}@${ctx.tenant().domain}`,mailNickname:`lab-${id.slice(0,8)}`,accountEnabled:true,createdDateTime:stamp};
  if(f.key==="directory.groups")return {id,displayName:"Lab group",mailNickname:`group-${id.slice(0,8)}`,mailEnabled:false,securityEnabled:true,groupTypes:[],createdDateTime:stamp};
  if(f.key==="identity.clients")return {id:"lab-client",display_name:"Lab client",permissions:[]};
  if(f.key==="content.objects")return {text:"Synthetic evidence\n",repeat:1,media_type:"text/plain"};
  return {id};
}

async function edit(f,ctx,refresh,item=null) {
  if(!ctx.tenant())throw new Error("Select a tenant first.");
  const operation=el("select",{"aria-label":"Operation"},f.operations.map(o=>el("option",{value:o.key},`${o.key} · ${o.mode}`)));
  const parent=el("input",{value:item?.parent||""}),identity=el("input",{placeholder:"Named local identity"});
  const editor=el("textarea",{"aria-label":"Resource JSON",spellcheck:false,value:JSON.stringify(item?.properties||defaultPayload(f,ctx),null,2)});
  const fields=el("div",{class:"form-grid"}),notes=el("p",{class:"hint"}),preview=el("div");
  const replace=el("input",{type:"checkbox"}),apply=button("Apply change",commit,"primary");apply.disabled=true;
  let receipt=null;
  function invalidate(){receipt=null;apply.disabled=true;preview.replaceChildren();}
  function op(){return f.operations.find(o=>o.key===operation.value);}
  function formFields(){
    const value=JSON.parse(editor.value);
    fields.replaceChildren();
    const keys=[...new Set([...(op().input_schema.required||[]),...Object.keys(value)])].filter(k=>["string","number","boolean"].includes(typeof value[k])).slice(0,8);
    keys.forEach(key=>{
      const input=el("input",{value:String(value[key])});
      input.addEventListener("input",()=>{const body=JSON.parse(editor.value);body[key]=typeof value[key]==="number"?Number(input.value):typeof value[key]==="boolean"?input.value==="true":input.value;editor.value=JSON.stringify(body,null,2);invalidate();});
      fields.append(field(key,input));
    });
  }
  function changeOperation(){
    const o=op();identity.disabled=!o.identity_required;
    notes.textContent=`${o.mode === "service"?"Public API action using the selected test identity":"Administrative fixture operation"} · ${o.method} ${o.path_template} · ${o.api_version}`;
    if(!item && o.mode==="service") {
      const variables={}; for(const match of o.path_template.matchAll(/\{([^}]+)\}/g))variables[match[1]]="";
      editor.value=JSON.stringify({variables,body:{}},null,2);
    }
    invalidate();formFields();
  }
  async function inspect(){
    receipt=await api(`/_mock/tenants/${ctx.tenant().id}/resources/${f.key}/preview`,{method:"POST",json:{operation:operation.value,logical_key:crypto.randomUUID(),payload:JSON.parse(editor.value),parent:parent.value||null,identity_ref:identity.value||null}});
    preview.replaceChildren(el("h3",{},"Review this change"),el("p",{},`Effect: ${receipt.effect}`),jsonView({affected_ids:receipt.affected_ids,inspection:receipt.inspection}));
    apply.disabled=false;
  }
  async function commit(){
    if(!receipt)return;
    apply.disabled=true;
    try{await api(`/_mock/tenants/${ctx.tenant().id}/resources/${f.key}/apply`,{method:"POST",json:{mutation:receipt.mutation,receipt:receipt.receipt,allow_replace:replace.checked}});document.querySelector("#editor").close();ctx.notice("Change applied. Refreshing the service view.");await refresh();}
    catch(error){apply.disabled=false;throw error;}
  }
  operation.addEventListener("change",changeOperation);
  [editor,parent,identity].forEach(node=>node.addEventListener("input",invalidate));
  dialog(item?"Edit resource":"Add resource",[el("div",{class:"form-grid"},field("Operation",operation),field("Scope / parent",parent),field("Test identity",identity)),notes,fields,field("Resource JSON",editor),el("p",{class:"hint"},"JSON is validated against the selected operation. Public actions require their real local API permissions."),el("div",{class:"actions"},button("Preview change",inspect),field("Allow the previewed replacement",replace,"check"),apply),preview]);
  changeOperation();
}

async function upload(ctx,refresh){
  if(!ctx.tenant())throw new Error("Select a tenant before uploading content.");
  const input=el("input",{type:"file"}),status=el("p",{class:"hint"});
  dialog("Upload lab content",[field("File",input),el("p",{class:"hint"},"Browser uploads are limited to 16 MiB. Larger files can use the chunked content API. Uploaded content is downloaded as an attachment."),button("Upload",async()=>{
    const file=input.files[0];if(!file)throw new Error("Select a file.");if(file.size>16*1024*1024)throw new Error("Use the chunked API for files larger than 16 MiB.");
    const sha256=[...new Uint8Array(await crypto.subtle.digest("SHA-256",await file.arrayBuffer()))].map(b=>b.toString(16).padStart(2,"0")).join("");
    const base=`/_mock/tenants/${ctx.tenant().id}/content/uploads`;
    const start=await api(base,{method:"POST",json:{size:file.size,sha256,media_type:file.type||"application/octet-stream"}});
    for(let offset=0,n=0;offset<file.size;offset+=start.chunkSize,n++){await api(`${base}/${start.uploadId}/chunks/${n}`,{method:"PUT",body:file.slice(offset,offset+start.chunkSize)});status.textContent=`Uploaded ${Math.min(offset+start.chunkSize,file.size)} / ${file.size} bytes`;}
    const result=await api(`${base}/${start.uploadId}/complete`,{method:"POST"});
    status.replaceChildren("Content ready. Use this content ID in a supported API operation: ",el("code",{},result.id));await refresh();
  },"primary"),status]);
}
