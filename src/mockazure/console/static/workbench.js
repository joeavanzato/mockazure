import {el,field,button,table,jsonView,query,heading,objectInput} from './api.js';
import {friendly,empty,disclosure} from './components.js';
import {Revision} from './state.js';
import {upload} from './uploads.js';
export async function workbench(root,ctx){
 const catalog=(await ctx.api('/_mock/console/workbench/catalog')).families;
 if(!ctx.tenant()){root.replaceChildren(heading('Test the interaction.','Choose a tenant to work with its supported APIs and fixtures.'),empty('Select a tenant','API requests always use an explicit tenant and test identity.'));return;}
 let family=catalog.find(f=>f.key===ctx.route.family)||catalog.find(f=>f.key==='directory.users'),mode=ctx.route.mode||'service',receipt=null,pending=false;
 let record=null;const revision=new Revision(),logicalKey=crypto.randomUUID();
 const familySelect=el('select',{'aria-label':'Resource family'},catalog.map(f=>el('option',{value:f.key},`${f.service} / ${friendly(f.key)}`)));familySelect.value=family.key;
 const operations=el('select',{'aria-label':'Operation'}),parent=el('input',{value:ctx.route.parent||''});
 const identity=el('select',{'aria-label':'Test identity'},el('option',{value:''},'Choose a named identity'));
 const clients=await ctx.api(`/_mock/tenants/${ctx.tenant().id}/resources/identity.clients?limit=500`);
 clients.items.forEach(i=>identity.append(el('option',{value:i.id},i.properties.display_name||i.id)));
 const editor=el('textarea',{'aria-label':'Resource JSON',spellcheck:false}),fields=el('div',{class:'form-grid'}),preview=el('section',{class:'result-panel'}),response=el('section',{class:'result-panel'}),notes=el('div'),identityDetails=el('div');
 const consent=el('input',{type:'checkbox'}),consentField=field('Allow this replacement',consent,'check');consentField.hidden=true;
 const prerequisite=el('p',{class:'hint',role:'status'},'Preview a change before applying it.'),apply=button('Apply change',commit,'lime');apply.disabled=true;
 const request=button('Preview change',run,'primary');
 const op=()=>family.operations.find(o=>o.key===operations.value);
 const modeButtons=el('div',{class:'section-tabs','aria-label':'Access mode'});
 function invalidate(){revision.invalidate();receipt=null;consent.checked=false;consentField.hidden=true;apply.disabled=true;preview.replaceChildren();response.replaceChildren();prerequisite.textContent='Inputs changed. Preview again before applying.';}
 function defaultPayload(){
  const o=op();if(!o)return {};
  if(o.mode==='service'){const variables={};for(const match of o.path_template.matchAll(/\{([^}]+)\}/g))variables[match[1]]=match[1]==='id'?record?.id||ctx.route.record||'':(['user','group','resource'].includes(match[1])?record?.id||'':'');return o.read?{variables,query:{}}:{variables,body:{}};}
  if(record)return record.properties;
  const id=crypto.randomUUID();if(family.key==='directory.users')return {id,displayName:'Lab user',userPrincipalName:`lab-${id.slice(0,8)}@${ctx.tenant().domain}`,mailNickname:`lab-${id.slice(0,8)}`,accountEnabled:true,createdDateTime:new Date().toISOString()};
  if(family.key==='directory.groups')return {id,displayName:'Lab group',mailNickname:`group-${id.slice(0,8)}`,mailEnabled:false,securityEnabled:true,groupTypes:[]};
  return {id};
 }
 function scalarFields(){
  fields.replaceChildren();let value;try{value=JSON.parse(editor.value);}catch{return;}
  const o=op();const containers=o?.mode==='service'?[['Path variables',value.variables,'variables'],[o.read?'Query parameters':'Body fields',value[o.read?'query':'body'],o.read?'query':'body']]:[['Fields',value,null]];
  for(const [label,object,key]of containers){if(!object||typeof object!=='object')continue;const props=key?o.input_schema?.properties?.[key]?.properties||{}:o.input_schema?.properties||{};
   const names=[...new Set([...Object.keys(object),...(key?[]:o.input_schema?.required||[])])].filter(name=>typeof object[name]!=='object'||object[name]===null).slice(0,12);
   for(const name of names){const original=object[name],type=typeof original==='boolean'?'boolean':typeof original==='number'?'number':props[name]?.type;
    const input=type==='boolean'?el('select',{},el('option',{value:'true'},'true'),el('option',{value:'false'},'false')):el('input',{type:type==='number'||type==='integer'?'number':'text'});input.value=original??'';
    input.addEventListener('input',()=>{invalidate();let body;try{body=JSON.parse(editor.value);}catch{prerequisite.textContent='Correct JSON before changing generated fields.';return;}
     const target=key?body[key]:body;if(!target)return;target[name]=type==='boolean'?input.value==='true':type==='number'||type==='integer'?Number(input.value):input.value;editor.value=JSON.stringify(body,null,2);
    });fields.append(field(`${label} / ${name}`,input));
   }
  }
  const o2=op(),containerKey=o2?.mode==='service'?(o2.read?'query':'body'):null;
  const schema=containerKey?o2?.input_schema?.properties?.[containerKey]?.properties:o2?.input_schema?.properties;
  const target=containerKey?value[containerKey]:value;
  if(target&&typeof target==='object'){
   const choices=Object.keys(schema||{}).filter(k=>!(k in target));
   const name=choices.length?el('select',{},choices.map(k=>el('option',{value:k},k))):el('input',{placeholder:'Property name'});
   const add=button('Add field',()=>{const body=objectInput(editor),part=containerKey?body[containerKey]:body,key=name.value.trim();if(!key||['__proto__','constructor','prototype'].includes(key))throw new Error('Choose a valid property name.');if(key in part)throw new Error('This field already exists.');const type=schema?.[key]?.type;part[key]=type==='boolean'?false:type==='number'||type==='integer'?0:type==='array'?[]:type==='object'?{}:'';editor.value=JSON.stringify(body,null,2);invalidate();scalarFields();});
   fields.append(field(containerKey==='query'?'Query parameter':'Add a property',name),add);
  }
 }
 function operationChanged(){invalidate();const o=op();if(!o){request.disabled=true;editor.disabled=true;notes.replaceChildren(empty('No operations in this mode','Choose another access mode or resource family.'));return;}
  request.disabled=false;editor.disabled=false;identity.disabled=!o.identity_required;request.textContent=o.read?'Run request':'Preview change';apply.hidden=Boolean(o.read);
  editor.value=JSON.stringify(defaultPayload(),null,2);scalarFields();
  notes.replaceChildren(el('div',{class:'api-path'},el('span',{class:'badge'},o.method),el('code',{},o.path_template),el('span',{class:'badge'},o.api_version)),el('p',{class:'hint'},o.mode==='service'?'Runs against the local emulated API as the selected identity. The server enforces its permissions.':'Administrative fixture operation. This does not demonstrate Microsoft API authorization.'),disclosure('Supported operations & limits',...family.limitations.map(n=>el('p',{class:'hint'},n)),el('p',{class:'hint'},'Contract references: '+family.contract_refs.join(', '))));
  prerequisite.textContent=o.read?'Read requests execute directly.':'MockAzure previews are local review controls, not Microsoft API dry-run endpoints.';
 }
 function chooseMode(next){mode=next;operations.replaceChildren(...family.operations.filter(o=>o.mode===mode).map(o=>el('option',{value:o.key},`${o.method} · ${friendly(o.key)}`)));if(ctx.route.operation&&family.operations.some(o=>o.key===ctx.route.operation&&o.mode===mode))operations.value=ctx.route.operation;
  if(record&&mode==='service'&&family.operations.some(o=>o.key==='update'))operations.value='update';
  for(const b of modeButtons.children)b.setAttribute('aria-pressed',String(b.dataset.mode===mode));operationChanged();
 }
 for(const [key,label]of [['service','Emulated API'],['fixture','Lab administration']]){const b=button(label,()=>chooseMode(key));b.dataset.mode=key;modeButtons.append(b);}
 async function run(){
  if(pending||!op())return;const o=op(),payload=objectInput(editor);if(o.identity_required&&!identity.value)throw new Error('Choose a named test identity.');
  invalidate();const ticket=revision.capture();pending=true;request.disabled=true;prerequisite.textContent=o.read?'Running local request…':'Preparing server preview…';
  try{
   const base=`/_mock/tenants/${ctx.tenant().id}/resources/${family.key}`;
   if(o.read){const result=await ctx.api(`/_mock/console/tenants/${ctx.tenant().id}/resources/${family.key}/read`,{method:'POST',json:{operation:o.key,identity_ref:identity.value,payload}});if(!ctx.active()||!revision.current(ticket))return;
    response.replaceChildren(el('h2',{},'Response'),el('p',{class:result.status<400?'success':'error',role:'status'},`HTTP ${result.status} · ${result.method} ${result.path}`),jsonView(result));prerequisite.textContent='Response from the selected local API identity.';
   }else{const prepared=await ctx.api(base+'/preview',{method:'POST',json:{operation:o.key,logical_key:logicalKey,payload,parent:parent.value||null,identity_ref:o.identity_required?identity.value:null}});if(!ctx.active()||!revision.current(ticket))return;
    receipt=prepared;consentField.hidden=prepared.effect!=='replace-record';apply.disabled=!consentField.hidden;
    preview.replaceChildren(el('h2',{},'Review this change'),el('p',{class:'hint'},`${o.mode==='service'?'Emulated API':'Lab administration'} · ${ctx.tenant().display_name} · ${identity.value||'console administrator'}`),el('span',{class:'badge warning'},prepared.effect),el('p',{class:'hint'},'Affected IDs: '+prepared.affected_ids.filter(Boolean).join(', ')),...(prepared.warnings||[]).map(w=>el('p',{class:'warning'},w)),prepared.changes?.length?table(['field','kind','before','after'],prepared.changes):el('p',{class:'hint'},'No field-level differences reported by the server.'),disclosure('Review technical details',jsonView({inspection:prepared.inspection,mutation:prepared.mutation})));prerequisite.textContent=consentField.hidden?'Ready to apply this exact reviewed proposal.':'Review the replacement and explicitly acknowledge it to enable Apply.';
   }
  }finally{pending=false;if(ctx.active())request.disabled=!op();}
 }
 async function commit(){
  if(pending||!receipt||(!consentField.hidden&&!consent.checked))return;const reviewed=receipt;receipt=null;pending=true;apply.disabled=true;request.disabled=true;const ticket=revision.capture();prerequisite.textContent='Applying the reviewed change…';
  try{const result=await ctx.api(`/_mock/tenants/${ctx.tenant().id}/resources/${family.key}/apply`,{method:'POST',json:{mutation:reviewed.mutation,receipt:reviewed.receipt,allow_replace:consent.checked}});if(!ctx.active()||!revision.current(ticket))return;
   preview.replaceChildren();consentField.hidden=true;response.replaceChildren(el('h2',{},'Change applied'),el('p',{class:'success',role:'status'},'The server completed the reviewed operation.'),jsonView(result),button('Inspect service data',()=>ctx.go('resources',{family:family.key,parent:parent.value})),op()?.mode==='service'&&ctx.capabilities.hunting?button('View evidence',()=>ctx.go('hunting')):null);prerequisite.textContent='An API success alone does not prove an evidence record exists.';
  }catch(error){if(ctx.active()&&revision.current(ticket)){preview.replaceChildren();consentField.hidden=true;prerequisite.textContent=error.status?'The server rejected the change. Resolve the error and preview again.':'Outcome could not be confirmed. Inspect server state before preparing another change.';}throw error;
  }finally{pending=false;if(ctx.active())request.disabled=!op();}
 }
 consent.addEventListener('change',()=>{apply.disabled=!receipt||pending||(!consentField.hidden&&!consent.checked);});
 operations.addEventListener('change',operationChanged);editor.addEventListener('input',invalidate);parent.addEventListener('input',invalidate);
 identity.addEventListener('change',async()=>{invalidate();identityDetails.replaceChildren(el('p',{class:'hint'},identity.value?'Named identity: '+identity.value:'No identity selected.'));});
 familySelect.addEventListener('change',()=>ctx.go('workbench',{family:familySelect.value,mode}));
 if(ctx.route.record){const page=await ctx.api(`/_mock/tenants/${ctx.tenant().id}/resources/${family.key}?`+query({identifier:ctx.route.record,parent:ctx.route.parent}));record=page.items[0];if(!record)throw new Error('The selected record is no longer available. Return to Explore resources.');}
 if(!family.operations.some(o=>o.mode===mode))mode=family.operations[0]?.mode||mode;
 const contextCard=el('aside',{class:'panel context-card'},el('p',{class:'eyebrow'},'REQUEST CONTEXT'),el('h2',{},ctx.tenant().display_name),el('p',{class:'mono'},ctx.tenant().id),el('h3',{},'Test identity'),field('Test identity',identity),identityDetails,el('p',{class:'hint'},'Lab administrator access does not grant Graph or other Microsoft API permissions. No denied request is retried as an administrator.'),record?el('p',{class:'record-id mono'},'Target: '+record.id):null);
 root.replaceChildren(heading('Test the interaction.','Make the access context explicit. Review a change before it reaches the service.'),el('div',{class:'two-col'},el('section',{class:'panel'},modeButtons,el('div',{class:'form-grid'},field('Resource family',familySelect),field('Operation',operations),field('Scope / parent',parent)),notes,fields,disclosure('Advanced request JSON',field('Resource JSON',editor),button('Refresh generated fields',scalarFields)),el('div',{class:'actions'},request,button('Upload content',()=>upload(ctx,async()=>{},id=>{const payload=objectInput(editor);payload.content_id=id;editor.value=JSON.stringify(payload,null,2);invalidate();scalarFields();}))),preview,consentField,el('div',{class:'actions'},apply),prerequisite,response),contextCard));
 chooseMode(mode);return ()=>revision.invalidate();
}
