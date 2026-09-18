import { api, esc } from './studio.js';
let selectedFile=null, selectedFolder=null;
const treeEl=document.getElementById('artifactTree');
function render(node,level=0){
  const wrap=document.createElement('div');wrap.className='artifact-node';
  if(node.type==='folder'){
    const row=document.createElement('div');row.className='artifact-row folder-row';row.style.paddingLeft=`calc(7px + ${level*17}px)`;
    row.innerHTML=`<span class="artifact-chevron">▾</span><span class="artifact-icon">▰</span><span class="artifact-name">${esc(node.name)}</span><span class="artifact-actions"><a class="artifact-link" href="/artifacts/zip?path=${encodeURIComponent(node.path)}">ZIP</a></span>`;
    const children=document.createElement('div');(node.children||[]).forEach(c=>children.appendChild(render(c,level+1)));row.onclick=e=>{if(e.target.tagName==='A')return;children.classList.toggle('collapsed');row.querySelector('.artifact-chevron').textContent=children.classList.contains('collapsed')?'▸':'▾';};wrap.append(row,children);return wrap;
  }
  const row=document.createElement('div');row.className='artifact-row file-row';row.style.paddingLeft=`calc(7px + ${level*17}px)`;const kb=node.size>1024?(node.size/1024).toFixed(1)+' KB':node.size+' B';
  row.innerHTML=`<span class="artifact-spacer"></span><span class="artifact-icon">▤</span><span class="artifact-name">${esc(node.name)}</span><span class="artifact-size">${kb}</span><span class="artifact-actions"><button class="artifact-link">Preview</button><a class="artifact-link" href="${node.download}">Download</a></span>`;
  row.querySelector('button').onclick=()=>preview(node);wrap.append(row);return wrap;
}
async function load(){const p=await api('/artifacts/tree');treeEl.innerHTML='';if(!p.tree?.length){treeEl.innerHTML='<div class="artifact-empty">No artifacts yet.</div>';return;}p.tree.forEach(n=>treeEl.appendChild(render(n)));}
async function preview(n){selectedFile=n;selectedFolder=n.parent_path;const p=await api('/artifacts/preview?path='+encodeURIComponent(n.path));document.getElementById('previewEmpty').classList.add('hidden');document.getElementById('previewContent').classList.remove('hidden');document.getElementById('previewTitle').textContent=n.name;document.getElementById('previewMeta').textContent=n.path;document.getElementById('previewContent').textContent=p.content;const a=document.getElementById('downloadCurrent');a.href=n.download;a.classList.remove('disabled-link');const f=document.getElementById('downloadFolder');if(n.parent_path){f.href='/artifacts/zip?path='+encodeURIComponent(n.parent_path);f.classList.remove('disabled-link');}}
document.getElementById('refreshArtifacts')?.addEventListener('click',load);load().catch(e=>{treeEl.innerHTML='<div class="artifact-empty">'+esc(e.message)+'</div>';});
