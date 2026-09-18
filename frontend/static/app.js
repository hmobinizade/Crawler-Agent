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
    $('analyzeCurrent').disabled = true;
    $('files').textContent = 'No crawler generated yet.';
    $('crawlerCode').textContent = 'No crawler code generated yet.';
    $('json').textContent = 'Opening browser…';
    $('fields').innerHTML = '';
    $('approvalText').textContent = 'Opening the target page. No analysis will run yet.';
    $('humanHint').classList.add('hidden');
    setStatus('RUNNING');
    $('method').textContent = 'ROUTING';

    const url = $('url').value.trim();
    const template = JSON.parse($('template').value);

    addStep('Checking whether this host was already analyzed…');
    addStep('Running adaptive routing/preflight…');

    const r = await fetch('/discover', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({url, template})
    });
    const job = await r.json();
    if (!r.ok) {
      if (r.status === 409) throw new Error('This host was already analyzed/generated. Analyze each host only once.');
      throw new Error(job.detail || 'Request failed');
    }

    (job.discovery.trace || []).forEach(addStep);
    $('method').textContent = String(job.discovery.method || 'unknown').toUpperCase();
    setStatus(job.discovery.status);
    showWarning(job.discovery.missing_required || []);
    window.currentJob = job;

    if (job.needs_browser_analysis) {
      $('json').textContent = 'Browser session is ready. Prepare the current page manually, then analyze it.';
      $('approvalText').textContent = 'The browser is open. Interact with this exact page, then analyze the current DOM.';
      $('humanHint').classList.remove('hidden');
      $('analyzeCurrent').disabled = false;
      addStep('Interactive browser session opened. Waiting for human preparation.');
      setStatus('NEEDS_HUMAN');
      return;
    }

    await renderDiscovery(job);
  } catch (e) {
    setStatus('FAILED');
    addStep('ERROR: ' + e.message);
    $('approvalText').textContent = 'The discovery request failed.';
  }
};

$('analyzeCurrent').onclick = async () => {
  if (!window.currentJob) return;
  try {
    $('analyzeCurrent').disabled = true;
    $('confirm').disabled = true;
    $('humanHint').classList.add('hidden');
    $('approvalText').textContent = 'Analyzing the current browser page…';
    $('json').textContent = 'Analyzing current page…';
    setStatus('RUNNING');
    addStep('Analyzing current browser page — navigation is locked during this step.');

    const r = await fetch('/analyze-current/' + window.currentJob.id, {method: 'POST'});
    const job = await r.json();
    if (!r.ok) throw new Error(job.detail || 'Current-page analysis failed');

    window.currentJob = job;
    await renderDiscovery(job);
    $('confirm').disabled = false;
    $('approvalText').textContent = job.discovery.status === 'INCOMPLETE'
      ? 'Some fields are missing, but approval is still allowed.'
      : 'Review the extraction and approve when it looks correct.';
  } catch (e) {
    $('analyzeCurrent').disabled = false;
    setStatus('FAILED');
    addStep('ERROR: ' + e.message);
    $('approvalText').textContent = 'Current-page analysis failed.';
  }
};

async function renderDiscovery(job) {
  (job.discovery.trace || []).forEach(addStep);
  $('method').textContent = String(job.discovery.method || 'unknown').toUpperCase();
  setStatus(job.discovery.status);
  showWarning(job.discovery.missing_required || []);
  // Approval is allowed for READY and INCOMPLETE results. Only FAILED or
  // a still-pending human browser gate keeps the button disabled.
  $('confirm').disabled = job.discovery.status === 'FAILED' || job.needs_browser_analysis === true;
  await typeText($('json'), JSON.stringify(job.discovery.proposed_json || {}, null, 2), 2);

  $('fields').innerHTML = (job.discovery.fields || []).map(f => `
    <div class="field">
      <div class="field-name">${escapeHtml(f.path)}</div>
      <div class="field-value">${escapeHtml(String(f.value ?? '—').slice(0, 600))}</div>
      <div class="field-status ${f.found ? 'ok' : 'bad'}">${f.found ? 'FOUND' : 'MISSING'}</div>
    </div>
  `).join('') || '<div class="field"><div class="field-value">No fields returned.</div></div>';

  if (job.discovery.status === 'READY') {
    $('approvalText').textContent = 'All requested fields were found. You can generate the crawler.';
  } else if (job.discovery.status === 'INCOMPLETE') {
    $('approvalText').textContent = 'Some fields are missing, but you can still approve and generate the crawler.';
    addStep('Result is incomplete. Approval is still allowed.');
  } else if (job.discovery.status === 'NEEDS_HUMAN') {
    $('approvalText').textContent = 'Human attention may be required before the crawler is reliable.';
  } else {
    $('approvalText').textContent = 'Review the result before generating the crawler.';
  }
}

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

$('useCurrentStructure').onclick = () => {
  if (!window.currentJob) {
    addStep('No current discovery structure is available yet.');
    return;
  }
  $('structureInput').value = JSON.stringify(window.currentJob.discovery || {}, null, 2);
  addStep('Current extraction structure copied into the code-only generator.');
};

$('generateCodeOnly').onclick = async () => {
  try {
    const raw = $('structureInput').value.trim();
    if (!raw) throw new Error('Paste an extraction structure first.');
    const structure = JSON.parse(raw);
    $('generateCodeOnly').disabled = true;
    $('approvalText').textContent = 'Building deterministic crawler from the extraction structure…';
    addStep('Building crawler from the validated extraction structure…');

    const r = await fetch('/generate-code', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({structure})
    });
    const payload = await r.json();
    if (!r.ok) throw new Error(payload.detail || 'Code generation failed');

    await typeText($('files'), (payload.files || []).join('\n'), 5);
    await typeText($('crawlerCode'), payload.code || '', 1);
    setStatus(payload.validation?.ok ? 'READY' : 'FAILED');
    $('method').textContent = String(payload.runtime || 'crawler').toUpperCase();
    $('approvalText').textContent = payload.validation?.ok
      ? 'Crawler generated deterministically and validated; LLM is used only if repair is needed.'
      : 'Crawler generation completed with validation errors.';
    addStep('Crawler built from the supplied structure; LLM repair is fallback-only.');
  } catch (e) {
    $('approvalText').textContent = 'Code generation failed.';
    addStep('ERROR: ' + e.message);
  } finally {
    $('generateCodeOnly').disabled = false;
  }
};


async function loadArtifacts() {
  const treeEl = $('artifactTree');
  if (!treeEl) return;
  try {
    const r = await fetch('/artifacts/tree');
    const payload = await r.json();
    if (!r.ok) throw new Error(payload.detail || 'Artifact tree failed');
    treeEl.innerHTML = '';
    if (!payload.tree?.length) {
      treeEl.innerHTML = '<div class="artifact-empty">No generated artifacts yet.</div>';
      return;
    }
    payload.tree.forEach(node => treeEl.appendChild(renderArtifactNode(node)));
  } catch (e) {
    treeEl.innerHTML = `<div class="artifact-empty">${escapeHtml(e.message)}</div>`;
  }
}

function renderArtifactNode(node, level = 0) {
  const wrap = document.createElement('div');
  wrap.className = 'artifact-node';
  wrap.style.setProperty('--level', level);

  if (node.type === 'folder') {
    const head = document.createElement('div');
    head.className = 'artifact-row folder-row';
    head.innerHTML = `<span class="artifact-chevron">▾</span><span class="artifact-icon">▰</span><span class="artifact-name">${escapeHtml(node.name)}</span>`;
    const children = document.createElement('div');
    children.className = 'artifact-children';
    (node.children || []).forEach(child => children.appendChild(renderArtifactNode(child, level + 1)));
    head.onclick = () => {
      children.classList.toggle('collapsed');
      head.querySelector('.artifact-chevron').textContent = children.classList.contains('collapsed') ? '▸' : '▾';
    };
    if (node.path.split('/').length > 1) {
      const actions = document.createElement('span');
      actions.className = 'artifact-actions';
      actions.innerHTML = `<a class="artifact-download folder-download" href="/artifacts/zip?path=${encodeURIComponent(node.path)}">Download ZIP</a>`;
      head.appendChild(actions);
    }
    wrap.appendChild(head);
    wrap.appendChild(children);
    return wrap;
  }

  const row = document.createElement('div');
  row.className = 'artifact-row file-row';
  const kb = node.size >= 1024 ? `${(node.size / 1024).toFixed(1)} KB` : `${node.size} B`;
  row.innerHTML = `<span class="artifact-spacer"></span><span class="artifact-icon">▤</span><span class="artifact-name">${escapeHtml(node.name)}</span><span class="artifact-size">${kb}</span><span class="artifact-actions"><button class="artifact-link preview-link">Preview</button><a class="artifact-link" href="${node.download}">Download</a></span>`;
  row.querySelector('.preview-link').onclick = () => previewArtifact(node.path, node.name);
  wrap.appendChild(row);
  return wrap;
}

async function previewArtifact(path, name) {
  const card = $('artifactPreview');
  try {
    const r = await fetch('/artifacts/preview?path=' + encodeURIComponent(path));
    const payload = await r.json();
    if (!r.ok) throw new Error(payload.detail || 'Preview failed');
    $('previewName').textContent = name;
    $('previewContent').textContent = payload.content;
    card.classList.remove('hidden');
    $('artifactPath').textContent = `generated/${path}`;
  } catch (e) {
    addStep('Artifact preview error: ' + e.message);
  }
}

$('refreshArtifacts')?.addEventListener('click', loadArtifacts);
$('closePreview')?.addEventListener('click', () => $('artifactPreview')?.classList.add('hidden'));
loadArtifacts();
