import { api, typeText, esc, setStatus, step } from './studio.js';

const url = document.getElementById('url');
const template = document.getElementById('template');
const button = document.getElementById('discover');
let lastJob = null;

const params = new URLSearchParams(location.search);
if (params.get('url')) url.value = params.get('url');

function renderFields(fields=[]) {
  const el = document.getElementById('fields');
  el.innerHTML = fields.length ? fields.map(f => `
    <div class="field">
      <div class="field-name">${esc(f.path)}</div>
      <div class="field-value"><div class="field-top"><span class="type-chip">${esc(f.selector_type || 'none')}</span> <span>${esc(f.selector || '—')}</span></div><div>${esc(JSON.stringify(f.value ?? null).slice(0, 500))}</div></div>
      <div class="field-status ${f.found ? 'ok' : 'bad'}">${f.found ? 'FOUND' : 'MISSING'}</div>
    </div>`).join('') : '<div class="empty-cell">No fields returned.</div>';
}

button.onclick = async () => {
  button.disabled = true; document.getElementById('steps').innerHTML='';
  setStatus('status','RUNNING'); document.getElementById('method').textContent='ROUTING';
  document.getElementById('json').textContent='Running discovery…'; document.getElementById('fields').innerHTML='';
  try {
    const parsed = JSON.parse(template.value);
    step('steps','Checking host history and running static preflight…');
    const job = await api('/discover',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:url.value.trim(),template:parsed})});
    lastJob=job; window.lastDiscoveryJob=job;
    (job.discovery.trace||[]).forEach(x=>step('steps',x));
    document.getElementById('method').textContent=(job.discovery.method||'—').toUpperCase();
    setStatus('status',job.discovery.status);
    await typeText(document.getElementById('json'),JSON.stringify(job.discovery.proposed_json||{},null,2),1);
    renderFields(job.discovery.fields||[]);
    document.getElementById('jobTitle').textContent=`${job.discovery.host} · ${job.id}`;
    document.getElementById('jobMeta').textContent=`Saved under generated/${job.discovery.host}/job_${job.id}`;
    document.getElementById('openCodegen').href='/codegen?job='+encodeURIComponent(job.id);
    document.getElementById('openCodegen').classList.remove('disabled-link');
    document.getElementById('openCrawler').href='/crawler?job='+encodeURIComponent(job.id); document.getElementById('openCrawler').classList.remove('disabled-link');
    const missing=job.discovery.missing_required||[];
    const alert=document.getElementById('alert');
    if(missing.length){alert.classList.remove('hidden');alert.innerHTML=`<strong>Missing:</strong> ${missing.map(esc).join(', ')}. The analysis is still saved and can be edited in Codegen.`;} else {alert.classList.add('hidden');}
  } catch(e) {
    setStatus('status','FAILED'); step('steps','ERROR: '+e.message,'error-step'); document.getElementById('json').textContent='Discovery failed.';
  } finally { button.disabled=false; }
};
