import {el,field,button,table,query,heading,objectInput} from './api.js';
import {friendly,empty,disclosure} from './components.js';
import {Revision} from './state.js';
import {detail} from './resource-detail.js';
import {upload} from './uploads.js';
export async function resources(root,ctx){
 const catalog=(await ctx.api('/_mock/resources/catalog')).families;
 const serviceName=name=>({'Identity and permissions':'Entra','Exchange and mail':'Exchange','Mail and Exchange':'Exchange','sharepoint':'SharePoint','teams':'Teams','intune':'Intune','Management Activity':'Activity feed','Graph security':'Security','Evidence':'Security','Advanced hunting':'Security'}[name]||name);
 catalog.forEach(f=>f.service=serviceName(f.service));
 const selected=catalog.find(f=>f.key===ctx.route.family)||catalog.find(f=>f.key==='directory.users')||catalog[0];
 const service=selected.service,services=[...new Set(catalog.map(f=>f.service))].sort();
 const rail=el('nav',{class:'family-rail','aria-label':'Resource families'},catalog.filter(f=>f.service===service).map(f=>{const b=button(friendly(f.key.split('.').slice(1).join('.')),()=>ctx.go('resources',{family:f.key}));b.setAttribute('aria-current',String(f.key===selected.key));b.title=f.key;return b;}));
 const search=el('input',{type:'search',placeholder:'Search this page…','aria-label':'Search loaded page'});
 const parent=el('input',{value:ctx.route.parent||'',placeholder:'Optional parent or workload scope'}),filters=el('textarea',{value:'{}',rows:3,style:'min-height:80px'});
 const rows=el('div'),paging=el('div',{class:'paging'}),status=el('p',{class:'hint',role:'status'}),revision=new Revision();let continuation=null,items=[];
 const draw=()=>{const term=search.value.toLocaleLowerCase();const found=items.filter(item=>JSON.stringify(item.properties).toLocaleLowerCase().includes(term)||item.id.toLocaleLowerCase().includes(term));
  rows.replaceChildren(found.length?table([...new Set(['id',...selected.display_fields])].slice(0,6),found.map(i=>({id:i.id,...i.properties,_resource:i})),async row=>{const item=row._resource;ctx.push({family:selected.key,record:item.id,parent:item.parent||parent.value});await detail(selected,ctx,item,catalog);}):empty(term?'No matches on this page':'No records in this view',term?'Clear the page search to see these loaded records.':'Change the scope, add supported data, or compose a populated lab.'));
  status.textContent=`${found.length} shown · ${items.length} loaded · search applies to this page only`;
 };
 async function load(next=false){
  if(!ctx.tenant()){rows.replaceChildren(empty('Choose a tenant to explore','Select a tenant above or create one in Compose lab.'));return;}
  const filter=objectInput(filters);revision.invalidate();const ticket=revision.capture();loadButton.disabled=true;
  rows.replaceChildren(el('p',{class:'muted',role:'status'},'Loading '+friendly(selected.key)+'…'));
  try{const page=await ctx.api(`/_mock/tenants/${ctx.tenant().id}/resources/${selected.key}?`+query({limit:50,parent:parent.value,filters:JSON.stringify(filter),continuation:next?continuation:null}));if(!revision.current(ticket)||!ctx.active())return;
   items=page.items;continuation=page.continuation;draw();const more=button('Next page',()=>load(true));more.disabled=!continuation;paging.replaceChildren(el('span',{},'Up to 50 records per page'),el('div',{class:'actions'},button('First page',()=>load()),more));
  }catch(error){if(error.name!=='AbortError'&&ctx.active()){rows.replaceChildren(empty('Unable to load records',error.message),button('Retry',()=>load()));}}finally{if(revision.current(ticket))loadButton.disabled=false;}
 }
 const loadButton=button('Load',()=>load()),add=button('Add data',()=>ctx.go('workbench',{family:selected.key,mode:'fixture'}),'primary');add.disabled=!ctx.tenant()||!selected.operations.length;
 search.addEventListener('input',draw);
 [parent,filters].forEach(input=>input.addEventListener('input',()=>{revision.invalidate();continuation=null;paging.replaceChildren();loadButton.disabled=false;status.textContent='Scope or filters changed. Load to refresh.';}));
 const meta=disclosure('Supported operations & limits',el('p',{class:'hint'},selected.operations.length?selected.operations.map(o=>`${o.key} · ${o.mode==='service'?'Emulated API':'Lab administration'}`).join(' / '):'Browsing only: no mutations in this catalog.'),...selected.limitations.map(note=>el('p',{class:'hint'},note)),el('p',{class:'hint'},'Contract references: '+selected.contract_refs.join(', ')));
 root.replaceChildren(heading('Explore your cloud.','Find persisted objects, inspect their shape, and test a supported interaction.',[button('Upload content',()=>upload(ctx,load)),add]),
  el('div',{class:'service-tabs','aria-label':'Services'},services.map(name=>{const b=button(name,()=>ctx.go('resources',{family:catalog.find(f=>f.service===name).key}));b.setAttribute('aria-pressed',String(name===service));return b;})),
  el('div',{class:'explorer-layout'},rail,el('section',{class:'resource-content'},el('div',{class:'resource-title'},el('h2',{},friendly(selected.key.split('.').slice(1).join('.'))),el('span',{class:'badge'},'Lab administration')),
   el('div',{class:'api-path'},el('code',{},selected.key),el('code',{},selected.api_path)),
   el('div',{class:'toolbar'},field('Search loaded page',search),field('Scope / parent',parent),loadButton),disclosure('Server filters (JSON)',field('Filters (JSON)',filters)),meta,status,rows,paging)));
 await load();
 if(ctx.route.record&&ctx.active())await detail(selected,ctx,{id:ctx.route.record,parent:ctx.route.parent},catalog);
 return ()=>revision.invalidate();
}
