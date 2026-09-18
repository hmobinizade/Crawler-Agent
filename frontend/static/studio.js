const S = id => document.getElementById(id);
const pause = ms => new Promise(r => setTimeout(r, ms));
export async function typeText(el, text, delay = 2) {
  if (!el) return;
  text = String(text ?? '');
  el.textContent = '';
  const chunk = Math.max(1, Math.floor(text.length / 700));
  for (let i = 0; i < text.length; i += chunk) {
    el.textContent += text.slice(i, i + chunk);
    el.scrollTop = el.scrollHeight;
    if (delay) await pause(delay);
  }
}
export function esc(value) {
  return String(value ?? '').replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
}
export function setStatus(id, value) {
  const el = S(id); if (!el) return;
  el.textContent = value || 'IDLE';
  el.className = 'status-badge ' + (value === 'READY' ? 'ready' : value === 'INCOMPLETE' ? 'incomplete' : value === 'FAILED' ? 'failed' : value === 'RUNNING' ? 'running' : 'neutral');
}
export function step(id, text, cls='') {
  const box = S(id); if (!box) return;
  const node = document.createElement('div'); node.className = `step ${cls}`; node.textContent = text; box.appendChild(node);
  box.scrollTop = box.scrollHeight;
}
export async function api(url, options={}) {
  const r = await fetch(url, options);
  let data = null; try { data = await r.json(); } catch { data = {detail: await r.text()}; }
  if (!r.ok) throw new Error(data?.detail || `HTTP ${r.status}`);
  return data;
}
export async function loadJobs(select, includeCrawled=true) {
  if (!select) return;
  const payload = await api('/artifacts/jobs');
  select.innerHTML = '<option value="">Select an analyzed job…</option>';
  (payload.jobs || []).forEach(j => {
    if (!includeCrawled && j.has_results) return;
    const o = document.createElement('option'); o.value = j.job_id;
    o.textContent = `${j.domain} · ${j.job_id} · ${j.url || 'no url'}`;
    select.appendChild(o);
  });
  return payload.jobs || [];
}
export async function jobDetails(jobId) { return api('/artifacts/jobs/' + encodeURIComponent(jobId)); }
