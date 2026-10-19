import fs from 'node:fs';
import {ResourceGraphClient} from '@azure/arm-resourcegraph';
import {LogsQueryClient} from '@azure/monitor-query-logs';
import {MonitorClient} from '@azure/arm-monitor';
const arg = name => process.argv[process.argv.indexOf(name)+1];
const p=JSON.parse(fs.readFileSync(arg('--profile'),'utf8')), workflow=arg('--workflow');
const checks={};
function options(origin) {
  const endpoint=new URL(origin);
  if(endpoint.protocol!=='https:' || !['localhost','127.0.0.1'].includes(endpoint.hostname))throw Error('Local HTTPS required');
  return {endpoint:origin, endpointUrl:origin, retryOptions:{maxRetries:0}, additionalPolicies:[{position:'perCall', policy:{name:'local-only',sendRequest:async(req,next)=>{
    if(new URL(req.url).origin!==endpoint.origin)throw Error('Client escaped local origin');
    return next(req);
  }}}]};
}
const credential = file => ({getToken:async()=>({token:JSON.parse(fs.readFileSync(file,'utf8')).access_token,expiresOnTimestamp:Date.now()+1800000})});
const graph=new ResourceGraphClient(credential(p.token_file),options(p.endpoint));
const seen=[];let skipToken;
do {
  const result=await graph.resources({query:p.inventory_query,subscriptions:[p.subscription_id],options:{top:1,resultFormat:'objectArray',...(skipToken?{skipToken}:{})}});
  seen.push(...result.data.map(row=>row.id.toLowerCase()));skipToken=result.skipToken;
}while(skipToken);
if(JSON.stringify(seen.sort())!==JSON.stringify([...p.expected_resources].sort()))throw Error('Inventory paging differs');
checks.inventory_paging=true;
const monitor=new MonitorClient(credential(p.token_file),p.subscription_id,options(p.endpoint));
if(workflow==='write')await monitor.diagnosticSettings.createOrUpdate(p.source.replace(/^\//,''),p.setting,{workspaceId:p.workspace_resource,storageAccountId:p.account,logAnalyticsDestinationType:'Dedicated',logs:[{enabled:true,category:'StorageWrite'}]});
const got=await monitor.diagnosticSettings.get(p.source.replace(/^\//,''),p.setting);
if(got.workspaceId.toLowerCase()!==p.workspace_resource.toLowerCase() || !got.logs[0].enabled)throw Error('Diagnostic setting differs');
checks.diagnostic_settings=true;
if(workflow==='read'){
  const logs=new LogsQueryClient(credential(p.logs_token_file),{...options(p.logs_endpoint),endpoint:p.logs_endpoint+'/v1',audience:'https://api.loganalytics.io'});
  const result=await logs.queryWorkspace(p.workspace_id,p.logs_query,{duration:'P30D'});
  if(JSON.stringify(result.tables?.[0].rows)!==JSON.stringify([[p.expected_logs]]))throw Error('Logs query differs: '+JSON.stringify(result));
  checks.native_logs_query=checks.cross_language_reads=true;
}
const dependencies=JSON.parse(fs.readFileSync(new URL('./package.json',import.meta.url),'utf8')).dependencies;
const versions=Object.fromEntries(Object.keys(dependencies).map(name=>[name,JSON.parse(fs.readFileSync(new URL('./node_modules/'+name+'/package.json',import.meta.url),'utf8')).version]));
fs.writeFileSync(arg('--report'),JSON.stringify({ecosystem:'typescript',passed:true,assertions:checks,clientVersions:versions,transport:'sdk',workflow}));
