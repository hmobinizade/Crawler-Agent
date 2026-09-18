import { api, typeText, esc, setStatus, step, loadJobs, jobDetails } from './studio.js';
const select=document.getElementById('jobSelect'), structure=document.getElementById('structure');
const loadBtn=document.getElementById('loadJob'), generate=document.getElementById('generate');
let selected=null;

async function refreshJobs() { await loadJobs(select); const job=new URLSearchParams(location.search).get('job'); if(job){select.value=job; await loadSelected();} }
async function loadSelected(){
  if(!select.value) return; selected=await jobDetails(select.value); structure.value=JSON.stringify(selected.structure||{},null,2); document.getElementById('jobTitle').textContent=selected.job.url||selected.job.job_id; document.getElementById('jobMeta').textContent=`${selected.job.domain} · ${selected.job.job_id}`; step('steps','Loaded extraction structure from artifact storage.');
}
loadBtn.onclick=loadSelected; select.onchange=loadSelected;

generate.onclick=async()=>{
  generate.disabled=true; setStatus('status','RUNNING'); document.getElementById('runtime').textContent='CODEGEN'; document.getElementById('code').textContent='Generating…'; document.getElementById('files').textContent='';
  try{
    const raw=JSON.parse(structure.value); step('steps','Generating deterministic crawler from the extraction structure…');
    const payload=await api('/generate-code',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({structure:raw,source_job_id:select.value||null,url:raw.url||null})});
    await typeText(document.getElementById('code'),payload.code||'',0);
    await typeText(document.getElementById('files'),(payload.files||[]).join('\n'),0);
    const ok=payload.validation?.ok!==false; setStatus('status',ok?'READY':'FAILED');
    document.getElementById('runtime').textContent=String(payload.runtime||'crawler').toUpperCase();
    document.getElementById('jobTitle').textContent=`${payload.domain} · ${payload.job_id}`;
    document.getElementById('jobMeta').textContent='Crawler saved to the selected job folder.';
    document.getElementById('runLink').href='/crawler?job='+encodeURIComponent(payload.job_id); document.getElementById('runLink').classList.remove('disabled-link');
    const v=document.getElementById('validation');
    if(ok){v.classList.add('hidden');step('steps','Crawler generated successfully.');} else {v.classList.remove('hidden');v.textContent=JSON.stringify(payload.validation||{},null,2);step('steps','Validation reported errors.');}
  }catch(e){setStatus('status','FAILED');step('steps','ERROR: '+e.message,'error-step');document.getElementById('code').textContent='Generation failed.';} finally{generate.disabled=false;}
};
refreshJobs().catch(e=>step('steps','ERROR: '+e.message));
