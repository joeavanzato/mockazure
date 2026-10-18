import fs from 'node:fs';
import {BlobServiceClient, newPipeline as blobPipeline, StorageSharedKeyCredential as BlobKey} from '@azure/storage-blob';
import {ShareServiceClient, newPipeline as filePipeline, StorageSharedKeyCredential as FileKey} from '@azure/storage-file-share';
const option=name=>process.argv[process.argv.indexOf(name)+1];
const p=JSON.parse(fs.readFileSync(option('--profile'),'utf8'));
const origins=new Set([p.blob_endpoint,p.file_endpoint].map(x=>new URL(x).origin));
for(const endpoint of origins){const u=new URL(endpoint);if(u.protocol!=='https:'||!['localhost','127.0.0.1'].includes(u.hostname))throw Error('Local HTTPS required');}
const guard={create(next){return {sendRequest(request){if(!origins.has(new URL(request.url).origin))throw Error('Nonlocal request rejected');return next.sendRequest(request);}};}};
let created=0;
for(const service of ['blob','file']){
 const options={serviceVersion:'2025-11-05'};
 const pipeline=service==='blob'?blobPipeline(new BlobKey(p.account,p.key),{retryOptions:{maxTries:1}}):filePipeline(new FileKey(p.account,p.key),{retryOptions:{maxTries:1}});
 pipeline.factories.push(guard);
 const client=service==='blob'?new BlobServiceClient(p.blob_endpoint,pipeline,options):new ShareServiceClient(p.file_endpoint,pipeline,options);
 const name='typescript-evidence';
 const object=service==='blob'?client.getContainerClient(name):client.getShareClient(name);
 await object.create({metadata:{writer:'typescript'}});
 const read=await object.getProperties();if(read.metadata.writer!=='typescript')throw Error('Metadata differs');
 const list=service==='blob'?client.listContainers({includeMetadata:true}):client.listShares({includeMetadata:true});
 const names=[];for await(const row of list)names.push(row.name);
 if(!names.includes(name))throw Error('Created namespace missing');
 created++;
}
fs.writeFileSync(option('--report'),JSON.stringify({passed:true,created,clientVersions:{'@azure/storage-blob':'12.29.1','@azure/storage-file-share':'12.29.1'}},null,2));
