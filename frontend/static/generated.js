import { api, esc } from './studio.js?v=22';

const treeEl = document.getElementById('artifactTree');
// const previewEmpty = document.getElementById('previewEmpty');
const previewContent = document.getElementById('previewContent');
const previewTitle = document.getElementById('previewTitle');
const previewMeta = document.getElementById('previewMeta');
const downloadCurrent = document.getElementById('downloadCurrent');
const downloadFolder = document.getElementById('downloadFolder');
const artifactPath = document.getElementById('artifactPath');
const refreshButton = document.getElementById('refreshArtifacts');

function sizeLabel(size=0) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function clearPreview(message='Select a file to preview it here.') {
  previewTitle.textContent='Artifact preview';
  previewMeta.textContent=message;
  // previewEmpty.textContent=message;
  // previewEmpty.classList.remove('hidden');
  // previewContent.classList.add('hidden');
  // previewContent.textContent='';
  downloadCurrent.href='#';
  downloadCurrent.classList.add('disabled-link');
  downloadFolder.href='#';
  downloadFolder.classList.add('disabled-link');
}

function makeIcon(isFolder) {
  const span=document.createElement('span');
  span.className='artifact-icon';
  span.textContent=isFolder?'▰':'▤';
  return span;
}

function renderFolder(node, level=0, initiallyOpen=false) {
  const wrap=document.createElement('div');
  wrap.className='artifact-node';

  const row=document.createElement('div');
  row.className='artifact-row folder-row';
  row.style.paddingLeft=`calc(7px + ${level*17}px)`;

  const toggle=document.createElement('button');
  toggle.type='button';
  toggle.className='artifact-toggle';
  toggle.setAttribute('aria-label', initiallyOpen?'Collapse folder':'Expand folder');
  toggle.textContent=initiallyOpen?'▾':'▸';

  const icon=makeIcon(true);
  const name=document.createElement('span');
  name.className='artifact-name';
  name.textContent=node.name;

  const actions=document.createElement('span');
  actions.className='artifact-actions';
  const zip=document.createElement('a');
  zip.className='artifact-link';
  zip.href=`/artifacts/zip?path=${encodeURIComponent(node.path)}`;
  zip.textContent='ZIP';
  zip.addEventListener('click', e=>e.stopPropagation());
  actions.appendChild(zip);

  row.append(toggle,icon,name,actions);

  const children=document.createElement('div');
  children.className='artifact-children' + (initiallyOpen?'':' collapsed');
  for(const child of (node.children||[])) children.appendChild(renderNode(child, level+1));

  const setOpen=(open)=>{
    children.classList.toggle('collapsed', !open);
    toggle.textContent=open?'▾':'▸';
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('aria-label', open?'Collapse folder':'Expand folder');
    artifactPath.textContent=node.path || 'generated/';
  };

  toggle.setAttribute('aria-expanded', String(initiallyOpen));
  toggle.addEventListener('click', e=>{ e.stopPropagation(); setOpen(children.classList.contains('collapsed')); });
  row.addEventListener('click', ()=>setOpen(children.classList.contains('collapsed')));
  wrap.append(row,children);
  return wrap;
}

function renderFile(node, level=0) {
  const wrap=document.createElement('div');
  wrap.className='artifact-node';
  const row=document.createElement('div');
  row.className='artifact-row file-row';
  row.style.paddingLeft=`calc(7px + ${level*17}px)`;

  const spacer=document.createElement('span');
  spacer.className='artifact-spacer';
  const icon=makeIcon(false);
  const name=document.createElement('span');
  name.className='artifact-name';
  name.textContent=node.name;
  const size=document.createElement('span');
  size.className='artifact-size';
  size.textContent=sizeLabel(node.size);

  const actions=document.createElement('span');
  actions.className='artifact-actions';
  const preview=document.createElement('button');
  preview.type='button'; preview.className='artifact-link preview-btn'; preview.textContent='Preview';
  preview.addEventListener('click',()=>previewArtifact(node));
  const download=document.createElement('a');
  download.className='artifact-link'; download.href=node.download; download.textContent='Download'; download.download='';
  actions.append(preview,download);

  row.append(spacer,icon,name,size,actions);
  row.addEventListener('dblclick',()=>previewArtifact(node));
  wrap.append(row);
  return wrap;
}

function renderNode(node, level=0) {
  return node.type==='folder' ? renderFolder(node,level,level===0) : renderFile(node,level);
}

async function previewArtifact(node) {
  try {
    const p=await api('/artifacts/preview?path='+encodeURIComponent(node.path));
    // previewEmpty.classList.add('hidden');
    previewContent.classList.remove('hidden');
    previewTitle.textContent=node.name;
    previewMeta.textContent=`${node.path} · ${sizeLabel(node.size)}`;
    let content=p.content || '';
    if (/\.json$/i.test(node.name)) {
      try { content=JSON.stringify(JSON.parse(content), null, 2); } catch {}
    }
    previewContent.textContent=content;
    downloadCurrent.href=node.download;
    downloadCurrent.classList.remove('disabled-link');
    if (node.parent_path) {
      downloadFolder.href='/artifacts/zip?path='+encodeURIComponent(node.parent_path);
      downloadFolder.classList.remove('disabled-link');
    }
    artifactPath.textContent=node.path;
  } catch (err) {
    clearPreview(`Preview error: ${err.message}`);
  }
}

async function load() {
  treeEl.innerHTML='<div class="artifact-empty">Loading generated artifacts…</div>';
  clearPreview();
  try {
    const payload=await api('/artifacts/tree');
    const tree=Array.isArray(payload.tree)?payload.tree:[];
    treeEl.innerHTML='';
    if (!tree.length) {
      treeEl.innerHTML='<div class="artifact-empty"><strong>No generated artifacts yet.</strong><br>Run Discover, Codegen, or Crawler first.</div>';
      return;
    }
    for (const node of tree) treeEl.appendChild(renderNode(node,0));
    artifactPath.textContent='generated/';
  } catch (err) {
    treeEl.innerHTML=`<div class="artifact-empty">Could not load artifacts.<br><code>${esc(err.message)}</code></div>`;
  }
}


function setAllFolders(open) {
  document.querySelectorAll('#artifactTree .artifact-children').forEach(el=>el.classList.toggle('collapsed', !open));
  document.querySelectorAll('#artifactTree .artifact-toggle').forEach(el=>{ el.textContent=open?'▾':'▸'; el.setAttribute('aria-expanded', String(open)); });
}

document.getElementById('expandAll')?.addEventListener('click',()=>setAllFolders(true));
document.getElementById('collapseAll')?.addEventListener('click',()=>setAllFolders(false));

refreshButton?.addEventListener('click',()=>load());
load();
