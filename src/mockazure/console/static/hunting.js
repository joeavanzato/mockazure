import {api,el,field,button,table,heading,download} from "./api.js";

export async function hunting(root,ctx){
  const tenant=ctx.tenant();
  root.replaceChildren(heading("Hunt across your lab","Query connected evidence using a named Graph identity."));
  if(!tenant){root.append(el("p",{class:"hint"},"Select a tenant to open hunting."));return;}
  const base=`/_mock/tenants/${tenant.id}/hunting`;
  const [catalog,cookbook,identities]=await Promise.all([
    api(base+"/tables"),api(base+"/cookbook"),
    api(`/_mock/tenants/${tenant.id}/resources/identity.clients?limit=500`)
  ]);
  let disposed=false,last=null;
  const identity=el("select",{id:"hunting-identity"},el("option",{value:""},"Choose a test identity"),
    identities.items.map(item=>el("option",{value:item.id},item.properties.display_name||item.id)));
  const example=el("select",{id:"hunting-example"},cookbook.cases.map(item=>el("option",{value:item.id},item.title)));
  const query=el("textarea",{id:"hunting-query",maxLength:65536,spellcheck:false,rows:8,
    "aria-label":"Hunting query",value:cookbook.cases[0].query});
  const timespan=el("input",{id:"hunting-timespan",value:"P30D",maxLength:256,placeholder:"P7D"});
  const status=el("p",{class:"hint",role:"status","aria-live":"polite"},"Ready. No query has run.");
  const results=el("section",{"aria-label":"Query results"});
  const execute=button("Run query",async()=>{
    if(!identity.value)throw new Error("Choose a named test identity before running the query.");
    execute.disabled=true;last=null;exportButton.disabled=true;
    status.textContent="Querying local Graph…";results.replaceChildren();
    try{
      const request={Query:query.value};if(timespan.value)request.Timespan=timespan.value;
      const response=await api(base+"/query",{method:"POST",json:{identity_ref:identity.value,request}});
      if(disposed)return;
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
      if(!disposed){status.textContent="Query failed.";results.replaceChildren(el("p",{class:"error",role:"alert"},error.message));}
    }finally{execute.disabled=false;}
  },"primary");
  const exportButton=button("Download JSON",()=>download("hunting-results.json",last),"small");exportButton.disabled=true;
  example.addEventListener("change",()=>{query.value=cookbook.cases.find(item=>item.id===example.value).query;});
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
  return ()=>{disposed=true;};
}
