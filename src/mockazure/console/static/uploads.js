import {el,field,button,dialog} from './api.js';
export async function upload(ctx,refresh=async()=>{},accept){
 if(!ctx.tenant())throw new Error('Select a tenant before uploading content.');
 const input=el('input',{type:'file'}),status=el('p',{class:'hint',role:'status'}),progress=el('progress',{max:1,value:0,'aria-label':'Upload progress'});let uploading=false;
 const startButton=button('Upload',async()=>{
  const file=input.files[0];if(!file)throw new Error('Select a file.');
  if(file.size>16777216)throw new Error('File exceeds 16 MiB (16,777,216 bytes). Use the chunked API for larger files.');
  uploading=true;startButton.disabled=true;input.disabled=true;status.textContent='Preparing upload…';
  try{const sha256=[...new Uint8Array(await crypto.subtle.digest('SHA-256',await file.arrayBuffer()))].map(b=>b.toString(16).padStart(2,'0')).join('');
   const base=`/_mock/tenants/${ctx.tenant().id}/content/uploads`,start=await ctx.api(base,{method:'POST',json:{size:file.size,sha256,media_type:file.type||'application/octet-stream'}});
   for(let offset=0,n=0;offset<file.size;offset+=start.chunkSize,n++){await ctx.api(`${base}/${start.uploadId}/chunks/${n}`,{method:'PUT',body:file.slice(offset,offset+start.chunkSize)});progress.value=Math.min(offset+start.chunkSize,file.size)/file.size;status.textContent=`Uploaded ${Math.min(offset+start.chunkSize,file.size).toLocaleString()} / ${file.size.toLocaleString()} bytes`;}
   const result=await ctx.api(`${base}/${start.uploadId}/complete`,{method:'POST'});if(!ctx.active())return;progress.value=1;status.replaceChildren('Content ready: ',el('code',{},result.id));
   if(accept)status.append(button('Use content ID',()=>{accept(result.id);document.querySelector('#editor').close();}));await refresh();
  }finally{uploading=false;startButton.disabled=false;input.disabled=false;}
 },'primary');
 input.addEventListener('change',()=>{if(uploading)return;progress.value=0;const file=input.files[0];startButton.disabled=Boolean(file&&file.size>16777216);status.textContent=file?`${file.name} · ${file.size.toLocaleString()} bytes${startButton.disabled?' · exceeds 16 MiB':''}`:'';});
 dialog('Upload lab content',[field('File',input),el('p',{class:'hint'},'Maximum browser upload: 16 MiB. A content ID becomes available only after the server accepts the upload.'),progress,status,startButton]);
}
