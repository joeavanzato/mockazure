import fs from 'node:fs';
import assert from 'node:assert/strict';
import {Client} from '@microsoft/microsoft-graph-client';
const option=name=>process.argv[process.argv.indexOf(name)+1];
const p=JSON.parse(fs.readFileSync(option('--profile'),'utf8'));
const mode=option('--mode');
assert(['write','read'].includes(mode));
const endpoint=new URL(p.endpoint);assert(endpoint.protocol==='https:'&&['localhost','127.0.0.1'].includes(endpoint.hostname));
const originalFetch=globalThis.fetch;
globalThis.fetch=(input,init)=>{
 const url=new URL(typeof input==='string'?input:input.url??input);
 assert.equal(url.origin,endpoint.origin);
 return originalFetch(input,{...init,redirect:'manual'});
};
const graph=Client.initWithMiddleware({baseUrl:p.endpoint,defaultVersion:'v1.0',customHosts:new Set([endpoint.hostname]),authProvider:{getAccessToken:async()=>p.token}});
const api=path=>graph.api(path);
const root=`/users/${p.owner}`;
const checks={};let objects={};
if(mode==='write'){
 const sub=await api('/subscriptions').post(p.subscription_body);
 const draft=await api(root+'/messages').post(p.message_body);assert(draft.id&&draft.isDraft);
 await api(root+'/messages/'+draft.id).patch({subject:p.label+' persistent'});
 assert.equal((await api(root+'/messages/'+draft.id).get()).subject,p.label+' persistent');
 await api(root+'/sendMail').post({message:p.send_body,saveToSentItems:true});
 const event=await api(root+'/events').post(p.event_body);
 const window={startDateTime:p.window_start,endDateTime:p.window_end};
 const instances=await api(root+'/events/'+event.id+'/instances').query(window).get();
 assert.equal(instances.value.length,3);
 assert(instances.value[0].start.dateTime.includes('T13:00:00'));
 assert(instances.value.slice(1).every(e=>e.start.dateTime.includes('T14:00:00')));
 const delta=await api(root+'/calendarView/delta').query(window).get();assert(delta['@odata.deltaLink']);
 await api(root+'/events/'+event.id).patch({subject:p.label+' updated'});
 const changed=await api(delta['@odata.deltaLink']).get();assert(changed.value.length);
 const target=`/users/${p.recipient}`;let mail,invite;
 for(let i=0;i<80;i++){
  mail=(await api(target+'/messages').get()).value.find(m=>m.subject===p.send_body.subject);
  invite=(await api(target+'/events').get()).value.find(e=>e.iCalUId===event.iCalUId);
  if(mail&&invite)break;await new Promise(r=>setTimeout(r,250));
 }
 assert(mail&&invite&&invite.id!==event.id);
 const attachments=(await api(target+'/messages/'+mail.id+'/attachments').get()).value;
 assert(attachments.some(a=>a.name==='evidence.bin'&&Buffer.from(a.contentBytes,'base64').equals(Buffer.from('local\0attachment'))));
 await api(target+'/events/'+invite.id+'/accept').post({sendResponse:true});
 await api('/subscriptions/'+sub.id).patch({expirationDateTime:new Date(Date.now()+10800000).toISOString()});
 objects={message:draft.id,event:event.id,subscription:sub.id,owner:p.owner};
 Object.assign(checks,{mail_roundtrip:true,calendar_recurrence_delta:true,meeting_response:true,subscription_management:true});
}else{
 const fixtures=JSON.parse(fs.readFileSync(p.fixtures,'utf8'));
 for(const fixture of Object.values(fixtures)){
  const other=`/users/${fixture.owner}`;
  assert.equal((await api(other+'/messages/'+fixture.message).get()).id,fixture.message);
  assert.equal((await api(other+'/events/'+fixture.event).get()).attendees[0].status.response,'accepted');
  await api(other+'/events/'+fixture.event).patch({subject:'Cross typescript'});
  assert.equal((await api(other+'/events/'+fixture.event).get()).subject,'Cross typescript');
 }
 const sub=fixtures.typescript.subscription;
 assert.equal((await api('/subscriptions/'+sub).get()).id,sub);
 await api('/subscriptions/'+sub+'/reauthorize').post();await api('/subscriptions/'+sub).delete();
 Object.assign(checks,{cross_language_reads:Object.keys(fixtures).length===4,subscription_management:true,persistent_read:true});
}
const pkg=JSON.parse(fs.readFileSync(new URL('./node_modules/@microsoft/microsoft-graph-client/package.json',import.meta.url),'utf8'));
fs.writeFileSync(option('--report'),JSON.stringify({passed:true,clientVersions:{'@microsoft/microsoft-graph-client':pkg.version},assertions:checks,objects},null,2)+'\n');
