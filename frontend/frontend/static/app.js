const $ = id => document.getElementById(id);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function typeText(el, text, delay = 2) {
  el.textContent = '';
  const chunk = Math.max(1, Math.floor(text.length / 600));
  for (let i = 0; i < text.length; i += chunk) {
    el.textContent += text.slice(i, i + chunk);
    el.scrollTop = el.scrollHeight;
    if (delay) await sleep(delay);
  }
}

function addStep(text) {
  const d = document.createElement('div');
  d.className = 'step';
  d.textContent = text;
  $('steps').appendChild(d);
}

function setStatus(status) {
  const el = $('statusBadge');
  el.textContent = status || 'IDLE';
  el.className = 'status-badge ' + (
    status === 'READY' ? 'ready' :
    status === 'INCOMPLETE' ? 'incomplete' :
    status === 'FAILED' ? 'failed' : 'neutral'
  );
}

function showWarning(missing) {
  const el = $('warning');
  if (!missing || missing.length === 0) {
    el.classList.add('hidden');
    el.textContent = '';
    return;
  }
  el.classList.remove('hidden');
  el.innerHTML = `<strong>Some requested fields are missing:</strong> ${missing.join(', ')}. You can still approve this result; the missing fields will remain <code>null</code> in the generated extraction. Review the result before continuing.`;
}

$('run').onclick = async () => {
  try {
    $('steps').innerHTML = '';
    $('confirm').disabled = true;
    $('files').textContent = 'No crawler generated yet.';
    $('json').textContent = 'Analyzing…';
    $('fields').innerHTML = '';
    $('approvalText').textContent = 'Analyzing the page and testing available extraction methods…';
    setStatus('RUNNING');
    $('method').textContent = 'WORKING';

    const url = $('url').value.trim();
    const template = JSON.parse($('template').value);

    addStep('Checking local crawlers…');
    await sleep(120);
    addStep('Running adaptive preflight…');

    const r = await fetch('/discover', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({url, template})
    });
    const job = await r.json();
    if (!r.ok) throw new Error(job.detail || 'Request failed');

    (job.discovery.trace || []).forEach(addStep);
    $('method').textContent = job.discovery.source === 'local'
      ? 'LOCAL'
      : String(job.discovery.method || 'unknown').toUpperCase();
    setStatus(job.discovery.status);
    showWarning(job.discovery.missing_required || []);

    await typeText($('json'), JSON.stringify(job.discovery.proposed_json, null, 2), 2);

    $('fields').innerHTML = (job.discovery.fields || []).map(f => `
      <div class="field">
        <div class="field-name">${escapeHtml(f.path)}</div>
        <div class="field-value">${escapeHtml(String(f.value ?? '—').slice(0, 600))}</div>
        <div class="field-status ${f.found ? 'ok' : 'bad'}">${f.found ? 'FOUND' : 'MISSING'}</div>
      </div>
    `).join('') || '<div class="field"><div class="field-value">No fields returned.</div></div>';

    window.currentJob = job;

    // Missing fields are warnings, not a hard stop.
    $('confirm').disabled = false;
    if (job.discovery.status === 'READY') {
      $('approvalText').textContent = 'All requested fields were found. You can generate the crawler.';
    } else if (job.discovery.status === 'INCOMPLETE') {
      $('approvalText').textContent = 'Some fields are missing, but you can still approve and generate the crawler.';
      addStep('Result is incomplete. Approval is still allowed.');
    } else if (job.discovery.status === 'NEEDS_HUMAN') {
      $('approvalText').textContent = 'A browser interaction or challenge may need human attention before generating the crawler.';
      addStep('Human attention may be required before the crawler is reliable.');
    } else {
      $('approvalText').textContent = 'Review the result before generating the crawler.';
    }
  } catch (e) {
    setStatus('FAILED');
    addStep('ERROR: ' + e.message);
    $('approvalText').textContent = 'The discovery request failed.';
  }
};

$('confirm').onclick = async () => {
  if (!window.currentJob) return;

  try {
    $('confirm').disabled = true;
    $('approvalText').textContent = 'Generating crawler…';

    const r = await fetch('/confirm/' + window.currentJob.id, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({approved: true})
    });
    const job = await r.json();
    if (!r.ok) throw new Error(job.detail || 'Confirm failed');

    window.currentJob = job;
    await typeText($('files'), (job.generated_files || []).join('\n') || 'No files generated.', 10);
    $('approvalText').textContent = 'Crawler generated successfully.';
    addStep('Crawler generated and ready to run.');
  } catch (e) {
    addStep('ERROR: ' + e.message);
    $('approvalText').textContent = 'Crawler generation failed.';
    $('confirm').disabled = false;
  }
};

function escapeHtml(value) {
  return value.replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
}
