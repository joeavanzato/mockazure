import { readFileSync, writeFileSync } from "node:fs";
import { ResourceManagementClient } from "@azure/arm-resources";
const sdkVersion=JSON.parse(readFileSync(new URL('../node_modules/@azure/arm-resources/package.json',import.meta.url),'utf8')).version;
const args=process.argv.slice(2);
const option=(name:string)=>{const i=args.indexOf(name);if(i<0)throw Error(`Missing ${name}`);return args[i+1];};
const profile=JSON.parse(readFileSync(option('--profile'),'utf8'));
const workflow=option('--workflow');
const endpoint=new URL(profile.endpoint);
if(endpoint.protocol!=='https:' || !['localhost','127.0.0.1'].includes(endpoint.hostname))throw Error('Local HTTPS endpoint required');
const token=JSON.parse(readFileSync(profile.token_file,'utf8')).access_token;
const client=new ResourceManagementClient({getToken:async()=>({token,expiresOnTimestamp:Date.now()+1800000})},profile.subscription_id,{endpoint:profile.endpoint,retryOptions:{maxRetries:2}});
client.pipeline.addPolicy({name:'localOnly',sendRequest:async(request,next)=>{
  if(new URL(request.url).origin!==endpoint.origin)throw Error('Client request escaped local origin');
  process.stderr.write(`${request.method} ${request.url}\n`);
  return next(request);
}}, {afterPhase:'Sign'});
// This SDK's URI template already includes the leading slash for resourceId.
const wireId=(id:string)=>id.replace(/^\//,'');
const started=Date.now();
let assertions:Record<string,number|boolean>={};
if(workflow==='cleanup'){
 for(const row of [...profile.resources].reverse())await client.resources.deleteById(wireId(row.id),row.version,{updateIntervalInMs:1000}).pollUntilDone();
 assertions={deleted:profile.resources.length};
}else{
 for(const row of profile.resources){
  const result=await client.resources.createOrUpdateById(wireId(row.id),row.version,row.body,{updateIntervalInMs:1000}).pollUntilDone();
  if(result.id?.toLowerCase()!==row.id.toLowerCase())throw Error('Created resource differs');
  const got=await client.resources.getById(wireId(row.id),row.version);
  if(got.id?.toLowerCase()!==row.id.toLowerCase())throw Error('Read resource differs');
 }
 for(const row of profile.read_resources){
  const got=await client.resources.getById(wireId(row.id),row.version);
  if(got.id?.toLowerCase()!==row.id.toLowerCase())throw Error('Cross-language resource differs');
 }
 const target=profile.resources.find((row:any)=>row.type==='Microsoft.Compute/virtualMachines');
 await client.resources.updateById(wireId(target.id),target.version,{tags:{writer:'typescript'}},{updateIntervalInMs:1000}).pollUntilDone();
 if((await client.resources.getById(wireId(target.id),target.version)).tags?.writer!=='typescript')throw Error('Update differs');
 assertions={created:profile.resources.length,cross_reads:profile.read_resources.length,updated:true};
}
writeFileSync(option('--report'),JSON.stringify({ecosystem:'typescript',clientVersions:{'@azure/arm-resources':sdkVersion},transport:'sdk',workflow,assertions,passed:true,elapsedSeconds:(Date.now()-started)/1000}));
