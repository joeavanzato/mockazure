import {el,button,table,jsonView,query,dialog,format} from './api.js';
import {friendly,sections,empty} from './components.js';
export async function detail(family,ctx,item,catalog){
 const page=await ctx.api(`/_mock/tenants/${ctx.tenant().id}/resources/${family.key}?`+query({identifier:item.id,parent:item.parent}));
 if(!ctx.active())return;
 if(!page.items.length){dialog('Resource unavailable',[empty('This record is unavailable','It may have been removed or the scope may be incorrect.'),button('Return to resources',()=>ctx.go('resources',{family:family.key}))]);return;}
 const full=page.items[0],body=el('div'),edit=button('Prepare an edit',()=>ctx.go('workbench',{family:family.key,record:full.id,parent:full.parent}),'primary');edit.disabled=!family.operations.length;
 function overview(){const values=Object.entries(full.properties).filter(([,v])=>v===null||typeof v!=='object').slice(0,14);body.replaceChildren(el('dl',{class:'record-summary'},values.map(([key,value])=>[el('dt',{},friendly(key)),el('dd',{},format(value))])));}
 let selectedSection='overview';
 const tabs=sections([['overview','Overview'],['related','Related resources'],['json','Raw JSON']],async key=>{
  selectedSection=key;
  if(key==='overview')overview();
  if(key==='json')body.replaceChildren(jsonView(full));
  if(key==='related'){
   if(!ctx.capabilities.relationships){body.replaceChildren(empty('Relationships unavailable','This server does not expose relationship navigation.'));return;}
   body.replaceChildren(el('p',{role:'status'},'Loading relationships…'));
   try{const links=await ctx.api(`/_mock/tenants/${ctx.tenant().id}/resources/${family.key}/related?`+query({identifier:full.id,parent:full.parent}));if(!ctx.active()||selectedSection!=='related')return;
    body.replaceChildren(links.items.length?table(['relation','direction','family','id'],links.items,row=>{if(catalog.some(f=>f.key===row.family))ctx.go('resources',{family:row.family,record:row.id,parent:row.parent});}):empty('No mapped relationships','Only relationships reported by this emulator are shown.'));
    if(links.truncated)body.append(el('p',{class:'hint'},'Showing the first 50 relationships. Use the service API for larger collections.'));
   }catch(error){if(ctx.active()&&selectedSection==='related'&&error.name!=='AbortError')body.replaceChildren(el('p',{class:'error'},error.message));}
  }
 });
 overview();const provenance=full.properties.provenance;
 const children=[el('p',{class:'eyebrow'},friendly(family.key)),el('p',{class:'record-id mono'},full.id),el('p',{class:'hint'},'Tenant '+ctx.tenant().display_name),el('span',{class:'badge'},provenance?'Reported provenance':'Provenance not reported'),provenance?jsonView(provenance):el('p',{class:'hint'},'A stored record alone does not establish whether an API operation produced evidence.'),tabs,body,el('div',{class:'actions'},edit,button('Copy ID',()=>navigator.clipboard.writeText(full.id)))];
 if(!family.operations.length)children.push(el('p',{class:'hint'},'No mutation is exposed for this family.'));
 if(family.key==='content.objects')children.push(el('a',{href:`/_mock/tenants/${ctx.tenant().id}/content/objects/${encodeURIComponent(full.id)}/$value`,download:full.id+'.bin'},'Download content'));
 children.push(el('p',{class:'hint'},family.api_path));
 dialog(full.properties.displayName||full.properties.name||full.properties.title||friendly(family.key),children,{drawer:true,onClose:()=>{if(ctx.active())ctx.replace({family:family.key,record:null,parent:full.parent});}});
}
