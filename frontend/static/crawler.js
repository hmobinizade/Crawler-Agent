import { api, typeText, esc, setStatus, step } from './studio.js';

const urlEl=document.getElementById('startUrl');
const structureEl=document.getElementById('structure');
const savedEl=document.getElementById('savedStructure');
const modeEl=document.getElementById('executionMode');
let structures=[];

function parsedStructure(){
  const raw=structureEl.value.trim();
  if(!raw) throw new Error('Extraction Structure is required.');
  let structure;
  try{ structure=JSON.parse(raw); }catch(e){ throw new Error('Structure JSON is invalid: '+e.message); }
  if(!structure || typeof structure!=='object' || Array.isArray(structure)) throw new Error('Structure must be a JSON object.');
  return structure;
}

function refreshExecutionMode(){
  try{
    const s=parsedStructure();
    const mode=(s.execution&&s.execution.mode)||'generic';
    if(mode==='custom'){ modeEl.textContent='CUSTOM • PLAYWRIGHT'; document.getElementById('executionHint').textContent='This structure points to a custom crawler script under generated/. The API will execute that script.'; }
    else { modeEl.textContent='GENERIC • PLAYWRIGHT'; document.getElementById('executionHint').textContent='This structure uses the shared Playwright runtime. No static Requests/BS4 path is used.'; }
  }catch{
    modeEl.textContent='WAITING';
    document.getElementById('executionHint').textContent='Paste a valid structure to detect its execution mode.';
  }
}

async function loadStructures(){
  const p=await api('/structures'); structures=p.structures||[]; savedEl.innerHTML='<option value="">Select a reusable structure…</option>';
  structures.forEach(s=>{const o=document.createElement('option');o.value=s.structure_id;o.textContent=`${s.domain} · ${s.structure_id}`;savedEl.appendChild(o);});
}

savedEl.addEventListener('change',async()=>{
  if(!savedEl.value) return;
  try{ const payload=await api('/structures/'+encodeURIComponent(savedEl.value)); structureEl.value=JSON.stringify(payload,null,2); const item=structures.find(x=>x.structure_id===savedEl.value); if(item&&!urlEl.value) urlEl.value=item.url||''; refreshExecutionMode(); }
  catch(e){step('steps','ERROR: '+e.message,'error-step');}
});
structureEl.addEventListener('input',refreshExecutionMode);

function renderRecords(records=[]){
  const body=document.querySelector('#results tbody');
  body.innerHTML=records.length?records.map(r=>`<tr><td><a href="${esc(r.url||'#')}" target="_blank" rel="noreferrer">${esc(r.url||'')}</a></td><td><span class="status-badge ${r.extraction_ok?'ready':'failed'}">${r.extraction_ok?'OK':'FAIL'}</span></td><td>${esc((r.missing_required||[]).join(', ')||'—')}</td><td><pre class="cell-preview">${esc(JSON.stringify(r.data||{},null,2).slice(0,1800))}</pre></td></tr>`).join(''):'<tr><td colspan="4" class="empty-cell">No records returned.</td></tr>';
}

document.getElementById('run').onclick=async()=>{
  const button=document.getElementById('run'); document.getElementById('steps').innerHTML='';
  try{
    const url=urlEl.value.trim(); if(!url) throw new Error('Start URL is required.');
    const structure=parsedStructure();
    button.disabled=true; setStatus('status','RUNNING'); document.getElementById('jsonResult').textContent='Running Playwright crawler…';
    refreshExecutionMode(); step('steps','Validating URL and Extraction Structure…');
    const payload=await api('/crawl',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url,structure,timeout_seconds:Number(document.getElementById('timeout').value||240),headless:document.getElementById('headless').checked})});
    document.getElementById('count').textContent=payload.record_count??0; document.getElementById('success').textContent=payload.success_count??0; document.getElementById('failed').textContent=payload.failed_count??0; document.getElementById('stats').textContent=`${String(payload.mode||'generic').toUpperCase()} · ${String(payload.runtime||'PLAYWRIGHT').toUpperCase()}`; setStatus('status',payload.failed_count?'INCOMPLETE':'READY');
    renderRecords(payload.records||[]); await typeText(document.getElementById('jsonResult'),JSON.stringify(payload.records||[],null,2),0);
    document.getElementById('runTitle').textContent=`${payload.record_count||0} records`; document.getElementById('runMeta').textContent=`Saved in generated/${payload.job_id||'crawl job'}/`;
    if(payload.result_download){const a=document.getElementById('resultDownload');a.href=payload.result_download;a.classList.remove('disabled-link');}
    step('steps',`Finished: ${payload.success_count}/${payload.record_count} successful records.`);
  }catch(e){ setStatus('status','FAILED'); step('steps','ERROR: '+e.message,'error-step'); document.getElementById('jsonResult').textContent='Crawler failed.'; } finally{ button.disabled=false; }
};

loadStructures().then(refreshExecutionMode).catch(e=>step('steps','ERROR: '+e.message,'error-step'));
