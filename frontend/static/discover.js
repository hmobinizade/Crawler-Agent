import { api, typeText, esc, setStatus, step } from './studio.js';

const url = document.getElementById('url');
const template = document.getElementById('template');
const button = document.getElementById('discover');
let lastEventCount = 0;
let pollHandle = null;

function setStage(stage) {
  document.querySelectorAll('.pipeline-stages span').forEach(el => {
    el.classList.remove('active');
    if (el.dataset.stage === stage) el.classList.add('active');
  });
}

function markDone(stage) {
  const stages = [...document.querySelectorAll('.pipeline-stages span')];
  const idx = stages.findIndex(x => x.dataset.stage === stage);
  stages.forEach((el,i) => { if (i <= idx) el.classList.add('done'); });
  setStage(stage);
}

function renderFields(fields=[]) {
  const el = document.getElementById('fields');
  el.innerHTML = fields.length ? fields.map(f => `
    <div class="field">
      <div class="field-name">${esc(f.path)}</div>
      <div class="field-value"><div class="field-top"><span class="type-chip">${esc(f.selector_type || 'none')}</span><span>${esc(f.selector || '—')}</span></div><div>${esc(JSON.stringify(f.value ?? null).slice(0, 500))}</div></div>
      <div class="field-status ${f.found ? 'ok' : 'bad'}">${f.found ? 'FOUND' : 'MISSING'}</div>
    </div>`).join('') : '<div class="empty-cell">No fields returned.</div>';
}

async function pollRun(runId) {
  const state = await api('/analysis/' + encodeURIComponent(runId));
  if (state.events?.length > lastEventCount) {
    for (const event of state.events.slice(lastEventCount)) {
      const prefix = event.stage ? `[${event.stage.toUpperCase()}] ` : '';
      step('steps', prefix + event.message, event.level === 'warning' ? 'warning-step' : event.level === 'error' ? 'error-step' : '');
      markDone(event.stage);
    }
    lastEventCount = state.events.length;
  }
  document.getElementById('runId').textContent = runId;
  setStatus('status', state.status === 'RUNNING' ? 'RUNNING' : state.status === 'DONE' ? state.result?.discovery?.status || 'READY' : state.status);
  if (state.stage) setStage(state.stage);

  if (state.status === 'DONE' && state.result) {
    clearInterval(pollHandle); pollHandle = null;
    const job = state.result;
    const d = job.discovery || {};
    const fields = d.fields || [];
    const missing = d.missing_required || [];
    document.getElementById('method').textContent = 'PLAYWRIGHT · BOUNDED AGENT';
    document.getElementById('verifiedCount').textContent = `${fields.filter(x => x.found).length}/${fields.length}`;
    document.getElementById('missingCount').textContent = String(missing.length);
    const analysisEvents = state.events.filter(x => ['analysis','verification','repair'].includes(x.stage));
    document.getElementById('roundCount').textContent = String(analysisEvents.length);
    await typeText(document.getElementById('json'), JSON.stringify(d.proposed_json || {}, null, 2), 1);
    renderFields(fields);
    document.getElementById('jobTitle').textContent = `${d.host} · ${job.id}`;
    document.getElementById('jobMeta').textContent = `Saved under generated/${d.host}/job_${job.id}`;
    const cg = document.getElementById('openCodegen'); cg.href = '/codegen?job=' + encodeURIComponent(job.id); cg.classList.remove('disabled-link');
    const cr = document.getElementById('openCrawler'); cr.href = '/crawler?job=' + encodeURIComponent(job.id); cr.classList.remove('disabled-link');
    const alert = document.getElementById('alert');
    if (missing.length) { alert.classList.remove('hidden'); alert.innerHTML = `<strong>Missing / unresolved:</strong> ${missing.map(esc).join(', ')}.`; }
    else alert.classList.add('hidden');
    button.disabled = false;
    return;
  }

  if (state.status === 'FAILED') {
    clearInterval(pollHandle); pollHandle = null;
    setStatus('status','FAILED');
    step('steps','ERROR: ' + (state.error || 'Analysis failed.'), 'error-step');
    button.disabled = false;
  }
}

button.onclick = async () => {
  button.disabled = true;
  lastEventCount = 0;
  document.getElementById('steps').innerHTML='';
  document.getElementById('fields').innerHTML='';
  document.getElementById('json').textContent='Starting…';
  document.getElementById('jobTitle').textContent='Analysis in progress';
  document.getElementById('jobMeta').textContent='The browser stays on the same page while the bounded analysis loop runs.';
  setStatus('status','RUNNING'); setStage('browser_prepare');
  try {
    const parsed = JSON.parse(template.value);
    const started = await api('/analysis/start', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:url.value.trim(),template:parsed})});
    await pollRun(started.run_id);
    const current = await api('/analysis/' + encodeURIComponent(started.run_id));
    if (current.status === 'RUNNING' || current.status === 'QUEUED') {
      pollHandle = setInterval(() => pollRun(started.run_id).catch(err => step('steps','ERROR: '+err.message,'error-step')), 450);
    }
  } catch (e) {
    setStatus('status','FAILED'); step('steps','ERROR: '+e.message,'error-step'); button.disabled=false;
  }
};
