from pathlib import Path
art=Path('/mnt/data/v18work/app/artifacts.py')
s=art.read_text(encoding='utf-8')
old="""                'size': stat.st_size,\n                'modified': stat.st_mtime,\n                'download': f'/artifacts/download?path={str(rel).replace(chr(92), \"/\")}',\n"""
new="""                'size': stat.st_size,\n                'modified': stat.st_mtime,\n                'parent_path': str(rel.parent).replace('\\\\','/') if str(rel.parent) != '.' else '',\n                'download': f'/artifacts/download?path={str(rel).replace(chr(92), \"/\")}',\n"""
if old not in s:
    raise SystemExit('artifact block not found')
art.write_text(s.replace(old,new),encoding='utf-8')

css=Path('/mnt/data/v18work/frontend/static/style.css')
c=css.read_text(encoding='utf-8')
append='''\n.topnav{display:flex;align-items:center;gap:6px;padding:5px;background:rgba(255,255,255,.78);border:1px solid var(--border);border-radius:13px;box-shadow:0 6px 18px rgba(32,48,74,.04)}\n.nav-link{padding:8px 11px;border-radius:9px;text-decoration:none;color:#6a7587;font-size:12px;font-weight:700}.nav-link:hover{background:#f3f6fb;color:#24314a}.nav-link.active{background:#182338;color:#fff}\n.generated-layout{display:grid;grid-template-columns:minmax(390px,.9fr) minmax(560px,1.55fr);gap:22px;align-items:start}.explorer-panel{padding:24px;min-height:calc(100vh - 190px)}.preview-panel{padding:24px;position:sticky;top:22px;min-height:calc(100vh - 190px)}.full-height{max-height:calc(100vh - 290px);min-height:320px}.preview-toolbar{display:flex;gap:8px;justify-content:flex-end;margin:-8px 0 14px}.disabled-link{pointer-events:none;opacity:.4}.preview-empty{min-height:520px;display:grid;place-items:center;border:1px dashed #d6deea;border-radius:16px;background:#fbfcfe;color:#98a4b5;font-size:13px}.standalone-preview{margin:0;min-height:520px;max-height:calc(100vh - 310px);overflow:auto;padding:18px;border:1px solid var(--border);border-radius:16px;background:#0f172a;color:#e2e8f0;font:12px/1.7 "Space Mono",Consolas,monospace;direction:ltr;text-align:left;white-space:pre-wrap;box-sizing:border-box}\n@media(max-width:1050px){.generated-layout{grid-template-columns:1fr}.preview-panel{position:relative;top:0}.topnav{order:2}}\n'''
if '.generated-layout{' not in c:
    c+=append
css.write_text(c,encoding='utf-8')
print('ok')
