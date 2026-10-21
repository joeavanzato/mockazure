import {el,field,button,table,heading,download} from "./api.js";
import {Revision} from "./state.js";

export async function hunting(root,ctx){
  const tenant=ctx.tenant();
  root.replaceChildren(heading("Follow the evidence.","Query supported evidence through an explicit, named Graph identity."));
  if(!tenant){root.append(el("p",{class:"hint"},"Select a tenant to open hunting."));return;}
  const base=`/_mock/tenants/${tenant.id}/hunting`;
  const [catalog,cookbook,identities]=await Promise.all([
    ctx.api(base+"/tables"),ctx.api(base+"/cookbook"),
    ctx.api(`/_mock/tenants/${tenant.id}/resources/identity.clients?limit=500`)
  ]);
  let disposed=false,last=null;const revision=new Revision();
  const identity=el("select",{id:"hunting-identity","aria-label":"Test identity"},el("option",{value:""},"Choose a test identity"),
    identities.items.map(item=>el("option",{value:item.id},item.properties.display_name||item.id)));
  const example=el("select",{id:"hunting-example"},cookbook.cases.map(item=>el("option",{value:item.id},item.title)));
  const query=el("textarea",{id:"hunting-query",maxLength:65536,spellcheck:false,rows:8,
    "aria-label":"Hunting query",value:cookbook.cases[0].query});
  const timespan=el("input",{id:"hunting-timespan",value:"P30D",maxLength:256,placeholder:"P7D"});
  const status=el("p",{class:"hint",role:"status","aria-live":"polite"},"Ready. No query has run.");
  const results=el("section",{"aria-label":"Query results"});
  const execute=button("Run query",async()=>{
    if(!identity.value)throw new Error("Choose a named test identity before running the query.");
    revision.invalidate();const ticket=revision.capture();
    execute.disabled=true;last=null;exportButton.disabled=true;
    status.textContent="Querying local Graph…";results.replaceChildren();
    try{
      const request={Query:query.value};if(timespan.value)request.Timespan=timespan.value;
      const response=await ctx.api(base+"/query",{method:"POST",json:{identity_ref:identity.value,request}});
      if(disposed||!ctx.active()||!revision.current(ticket))return;
      last=response.graph;exportButton.disabled=false;
      const count=last.results.length,shown=Math.min(200,count);
      status.textContent=`${count.toLocaleString()} rows returned · ${shown.toLocaleString()} shown · Evidence reference ${response.reference_time}`;
      results.replaceChildren(
        el("div",{class:"heading"},el("h2",{},"Results"),el("span",{class:"badge"},"GRAPH v1.0")),
        table(last.schema.map(column=>column.name),last.results.slice(0,200)),
        el("details",{},el("summary",{},"Result column types"),
          table(["name","type"],last.schema))
      );
      if(count>shown)results.append(el("p",{class:"hint"},"The display is limited to 200 rows. Download JSON for the complete returned result."));
    }catch(error){
      if(!disposed&&ctx.active()&&revision.current(ticket)){status.textContent="Query failed.";results.replaceChildren(el("p",{class:"error",role:"alert"},error.message));}
    }finally{execute.disabled=false;}
  },"primary");
  const exportButton=button("Download JSON",()=>download("hunting-results.json",last),"small");exportButton.disabled=true;
  function invalidate(){revision.invalidate();last=null;exportButton.disabled=true;results.replaceChildren();status.textContent="Inputs changed. Run the query to refresh results.";}
  example.addEventListener("change",()=>{query.value=cookbook.cases.find(item=>item.id===example.value).query;invalidate();});
  [identity,query,timespan].forEach(input=>input.addEventListener("input",invalidate));
  identity.value=ctx.drafts.get("hunting-identity:"+tenant.id)||"";
  identity.addEventListener("change",()=>{ctx.drafts.set("hunting-identity:"+tenant.id,identity.value);invalidate();});
  const selector=el("select",{"aria-label":"Hunting table"},catalog.tables.map(item=>el("option",{value:item.name},item.name)));
  const definition=el("div",{});
  function showSchema(){
    const selected=catalog.tables.find(item=>item.name===selector.value);
    definition.replaceChildren(
      el("p",{class:"hint"},`${selected.availability} · ${selected.entitlements.join(", ")} · ${Object.keys(selected.columns).length} columns`),
      el("p",{class:"hint"},selected.native_mappings.length?"Native mappings: "+selected.native_mappings.join(", "):"Fixture observations; no native sensor mapping."),
      table(["Column","Type"],Object.entries(selected.columns).map(([Column,Type])=>({Column,Type})))
    );
  }
  selector.addEventListener("change",showSchema);showSchema();
  root.append(
    el("div",{class:"hunting-layout"},
      el("section",{class:"card hunting-editor"},
        el("p",{class:"eyebrow"},"GRAPH / ADVANCED HUNTING"),
        el("div",{class:"form-grid"},field("Test identity",identity),field("Cookbook example",example)),
        field("KQL query",query),el("div",{class:"toolbar"},field("Timespan",timespan),execute,exportButton),
        el("p",{class:"hint"},"The selected identity needs ThreatHunting.Read.All and data access. Lab administrator access does not grant Graph permissions."),
        status,results),
      el("aside",{class:"card hunting-reference"},el("h2",{},"Table reference"),
        field("Table",selector),definition)
    )
  );
  return ()=>{disposed=true;revision.invalidate();};
}
