import {api,el,field,button,heading,query,table} from "./api.js";
export async function inspector(root,ctx){
  const catalog=await ctx.api("/_mock/console/inspector"),select=el("select",{"aria-label":"Inspector table"},catalog.tables.map(t=>el("option",{value:t.key},t.key))),rows=el("div"),paging=el("div",{class:"paging"});
  let continuation=null,revision=0;
  async function load(next=false){
    const ticket=++revision;
    if(!ctx.tenant()){rows.replaceChildren(el("div",{class:"empty"},"Select a tenant above."));return;}
    const descriptor=catalog.tables.find(t=>t.key===select.value),page=await ctx.api("/_mock/console/inspector?"+query({table_key:select.value,tenant_id:ctx.tenant().id,limit:50,continuation:next?continuation:null}));
    if(!ctx.active()||ticket!==revision)return;
    continuation=page.continuation;
    rows.replaceChildren(table(descriptor.columns,page.items.map(i=>({id:i.id,...i.properties}))));
    const more=button("Next page",()=>load(true));more.disabled=!continuation;paging.replaceChildren(el("span",{},`${page.items.length} rows · tenant scope · read only`),more);
  }
  select.addEventListener("change",()=>load().catch(ctx.error));
  root.replaceChildren(heading("Look under the surface.","A curated view of persisted service tables. Credentials, sessions, keys and unrestricted JSON columns are excluded."),el("div",{class:"toolbar"},field("Inspector table",select),button("First page",()=>load())),rows,paging);await load();
}
