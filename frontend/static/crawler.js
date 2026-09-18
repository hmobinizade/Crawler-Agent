import { api, typeText, esc, setStatus, step, loadJobs, jobDetails } from './studio.js';

const jobSelect=document.getElementById('jobSelect');
const structureSelect=document.getElementById('structureSelect');
const mode=document.getElementById('mode');
let jobs=[];

async function loadStructures(){
  const payload=await api('/structures');
  structureSelect.innerHTML='<option value="">Select a reusable structure…</option>';
  (payload.structures||[]).forEach(s=>{
    const o=document.createElement('option');o.value=s.structure_id;o.textContent=`${s.domain} · ${s.structure_id}`;structureSelect.appendChild(o);
  });
}

async function loadAll(){
  jobs=await loadJobs(jobSelect); await loadStructures();
  const q=new URLSearchParams(location.search); if(q.get('job')) jobSelect.value=q.get('job'); if(q.get('structure')) structureSelect.value=q.get('structure');
}

function renderRecords(records=[]){
  const body=document.querySelector('#results tbody');
  body.innerHTML=records.length ? records.map(r=>`<tr><td><a href="${esc(r.url||'#')}" target="_blank" rel="noreferrer">${esc(r.url||'')}</a></td><td><span class="status-badge ${r.extraction_ok?'ready':'failed'}">${r.extraction_ok?'OK':'FAIL'}</span></td><td>${esc((r.missing_required||[]).join(', ')||'—')}</td><td><pre class="cell-preview">${esc(JSON.stringify(r.data||{},null,2).slice(0,1400))}</pre></td></tr>`).join('') : '<tr><td colspan="4" class="empty-cell">No records returned.</td></tr>';
}

jobSelect.onchange=()=>{if(jobSelect.value) structureSelect.value='';};
structureSelect.onchange=()=>{if(structureSelect.value) jobSelect.value='';};


document.getElementById('run').onclick=async()=>{
  const selectedJob=jobSelect.value||null, selectedStructure=structureSelect.value||null;
  if(!selectedJob && !selectedStructure){step('steps','Choose a job or reusable structure first.','error-step');return;}
  const button=document.getElementById('run'); button.disabled=true; document.getElementById('steps').innerHTML=''; setStatus('status','RUNNING');
  document.getElementById('jsonResult').textContent='Running generic crawler…';
  try{
    step('steps', selectedStructure ? 'Loading reusable structure…' : 'Loading analysis structure…');
    const payload=await api('/crawl',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source_job_id:selectedJob,structure_id:selectedStructure,url:document.getElementById('startUrl').value.trim()||null,mode:mode.value,max_pages:Number(document.getElementById('maxPages').value||20),timeout_seconds:Number(document.getElementById('timeout').value||240),headless:document.getElementById('headless').checked})});
    document.getElementById('count').textContent=payload.record_count??0;document.getElementById('success').textContent=payload.success_count??0;document.getElementById('failed').textContent=payload.failed_count??0;document.getElementById('stats').textContent=`${String(payload.mode||mode.value).toUpperCase()} · ${String(payload.runtime||'').toUpperCase()}`;setStatus('status',payload.failed_count?'INCOMPLETE':'READY');
    renderRecords(payload.records||[]); await typeText(document.getElementById('jsonResult'),JSON.stringify(payload.records||[],null,2),0);
    document.getElementById('runTitle').textContent=`${payload.record_count||0} records`; document.getElementById('runMeta').textContent=`Saved in ${payload.job_id||'crawl job'}`;
    if(payload.result_download){const a=document.getElementById('resultDownload');a.href=payload.result_download;a.classList.remove('disabled-link');}
    step('steps',`Crawler finished: ${payload.success_count}/${payload.record_count} successful records.`);
  }catch(e){setStatus('status','FAILED');step('steps','ERROR: '+e.message,'error-step');document.getElementById('jsonResult').textContent='Crawler failed.';}finally{button.disabled=false;}
};

loadAll().catch(e=>step('steps','ERROR: '+e.message,'error-step'));
