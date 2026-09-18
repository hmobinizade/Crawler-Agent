from pathlib import Path
p=Path('/mnt/data/v18work/app/codegen.py')
s=p.read_text(encoding='utf-8')
old=s[s.index('def extract_collection(soup, spec):'):s.index('\ndef postprocess_exact_value', s.index('def extract_collection(soup, spec):'))]
new=r'''def _hybrid_jsonld_candidate(soup, candidate):
    """Support candidates like: script[type='application/ld+json'] path url."""
    m = re.match(r"^(?P<script>.+?)\s+path\s+(?P<path>.+)$", str(candidate).strip(), re.I)
    if not m:
        return None
    script_selector = m.group('script').strip()
    path = m.group('path').strip()
    try:
        scripts = soup.select(script_selector)
    except Exception:
        scripts = []
    values=[]
    for script in scripts:
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            payload=json.loads(raw.strip())
        except Exception:
            continue
        objects = payload if isinstance(payload, list) else [payload]
        for obj in objects:
            cur=obj
            for part in path.split('.'):
                cur = cur.get(part) if isinstance(cur, dict) else None
            if cur in (None,''):
                continue
            seq = cur if isinstance(cur,list) else [cur]
            for item in seq:
                if isinstance(item,dict):
                    item = item.get('url') or item.get('@id') or item.get('name') or item
                values.append(item)
    return values or None


def _collection_item_value(node, field_spec):
    """Resolve collection item fields such as @href and text."""
    if isinstance(field_spec, dict):
        q = field_spec.get('selector')
        attr = field_spec.get('attribute')
        ftype = field_spec.get('type') or field_spec.get('value_type')
    else:
        q = str(field_spec) if field_spec is not None else None
        attr = None
        ftype = None
    if not q or q == 'text' or ftype == 'text':
        return clean_text(node.get_text(' ', strip=True))
    if isinstance(q, str) and q.startswith('@'):
        return clean_text(node.get(q[1:]))
    if q in {'self', '.'}:
        return clean_text(node.get_text(' ', strip=True))
    try:
        child = node.select_one(q)
    except Exception:
        child = None
    if not child:
        return None
    if attr:
        return clean_text(child.get(attr))
    return clean_text(child.get_text(' ', strip=True))


def extract_collection(soup, spec):
    selector_type = spec.get('selector_type') or 'none'
    scope = _scope_root(soup, spec.get('scope_selector')) if spec.get('scope_selector') else soup
    if scope is None:
        return None

    if selector_type == 'jsonld':
        for candidate in candidate_selectors(spec):
            raw = jsonld_get_raw(soup, candidate)
            if raw in (None, ''):
                raw = _hybrid_jsonld_candidate(soup, candidate)
            if raw in (None, ''):
                continue
            raw_items = raw if isinstance(raw, list) else [raw]
            item_fields = spec.get('item_fields') or {}
            if item_fields and any(isinstance(x, dict) for x in raw_items):
                rows=[]
                for item in raw_items:
                    row={}
                    for key, fs in item_fields.items():
                        if isinstance(fs, dict):
                            path=fs.get('path') or fs.get('selector')
                        else:
                            path=str(fs)
                        if isinstance(path,str) and path.startswith('@'):
                            value=item.get(path[1:]) if isinstance(item,dict) else None
                        elif path == 'text':
                            value=item.get('name') or item.get('text') if isinstance(item,dict) else item
                        else:
                            value=item
                            for part in str(path).split('.'):
                                value=value.get(part) if isinstance(value,dict) else None
                        row[key]=clean_text(value) if not isinstance(value,(list,dict)) else value
                    rows.append(row)
                return rows
            values=[]
            for item in raw_items:
                if isinstance(item,dict):
                    values.append(item)
                else:
                    values.append(clean_text(item))
            return values or None
        return None

    if selector_type == 'text':
        value = extract_text_value(scope, spec.get('selector'), None)
        return [value] if value not in (None, '') else None

    item_selector = spec.get('item_selector') or spec.get('selector')
    item_fields = spec.get('item_fields') or {}
    candidates=[]
    for candidate in [item_selector, *(spec.get('selector_candidates') or [])]:
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    nodes=[]
    for selector in candidates:
        try:
            nodes = scope.select(selector)
        except Exception:
            nodes=[]
        if nodes:
            break
        hybrid = _hybrid_jsonld_candidate(soup, selector)
        if hybrid:
            # candidate is not a DOM collection; return structured values.
            if item_fields:
                rows=[]
                for item in hybrid:
                    if isinstance(item,dict):
                        row={}
                        for key,fs in item_fields.items():
                            path = fs.get('path') or fs.get('selector') if isinstance(fs,dict) else str(fs)
                            if path == 'text': value=item.get('name') or item.get('text')
                            elif isinstance(path,str) and path.startswith('@'): value=item.get(path[1:])
                            else:
                                value=item
                                for part in str(path).split('.'):
                                    value=value.get(part) if isinstance(value,dict) else None
                            row[key]=clean_text(value) if not isinstance(value,(list,dict)) else value
                        rows.append(row)
                if rows:
                    return rows
            return [x for x in hybrid if x not in (None,'')]
    if not nodes:
        return None
    out=[]
    for node in nodes[:500]:
        row={}
        if item_fields:
            for key,fs in item_fields.items():
                row[key]=_collection_item_value(node, fs)
        else:
            row={'text': clean_text(node.get_text(' ', strip=True))}
        if any(v not in (None,'') for v in row.values()):
            out.append(row)
    return out or None
'''
s=s.replace(old,new)
# Replace Playwright extract_collection function by locating its next def
start=s.index('def extract_collection(page,spec):')
end=s.index('\ndef extract_exact_page', start)
old2=s[start:end]
new2=r'''def _playwright_hybrid_candidate(page, candidate):
    m = re.match(r"^(?P<script>.+?)\s+path\s+(?P<path>.+)$", str(candidate).strip(), re.I)
    if not m:
        return None
    script_selector=m.group('script').strip(); path=m.group('path').strip(); values=[]
    try: scripts=page.locator(script_selector)
    except Exception: return None
    for i in range(scripts.count()):
        try: payload=json.loads(scripts.nth(i).inner_text(timeout=3000))
        except Exception: continue
        for obj in (payload if isinstance(payload,list) else [payload]):
            cur=obj
            for part in path.split('.'):
                cur=cur.get(part) if isinstance(cur,dict) else None
            if cur in (None,''): continue
            seq=cur if isinstance(cur,list) else [cur]
            for item in seq:
                if isinstance(item,dict): item=item.get('url') or item.get('@id') or item.get('name') or item
                values.append(item)
    return values or None


def _playwright_item_value(item, fs):
    if isinstance(fs,dict): q=fs.get('selector'); attr=fs.get('attribute')
    else: q=str(fs) if fs is not None else None; attr=None
    if not q or q=='text':
        return clean_text(item.inner_text(timeout=1500))
    if isinstance(q,str) and q.startswith('@'):
        return clean_text(item.get_attribute(q[1:], timeout=1500))
    child=item.locator(q).first
    if child.count()==0: return None
    if attr: return clean_text(child.get_attribute(attr,timeout=1500))
    return clean_text(child.inner_text(timeout=1500))


def extract_collection(page,spec):
    selector_type=spec.get('selector_type') or 'none'
    if selector_type == 'jsonld':
        for path in candidate_selectors(spec):
            value=jsonld_value(page,path)
            if value in (None,''):
                value=_playwright_hybrid_candidate(page,path)
            if value in (None,''): continue
            return value if isinstance(value,list) else [value]
        return None
    if selector_type == 'text':
        value=extract_text_page(page,spec.get('selector'),spec.get('scope_selector'))
        return [value] if value not in (None,'') else None
    scope=page.locator(spec.get('scope_selector')).first if spec.get('scope_selector') else page.locator('body')
    item_selector=spec.get('item_selector') or spec.get('selector')
    candidates=[]
    for candidate in [item_selector, *(spec.get('selector_candidates') or [])]:
        if candidate and candidate not in candidates: candidates.append(candidate)
    nodes=None
    for selector in candidates:
        try:
            loc=scope.locator(selector)
            if loc.count(): nodes=loc; break
        except Exception: pass
        hybrid=_playwright_hybrid_candidate(page,selector)
        if hybrid:
            item_fields=spec.get('item_fields') or {}; rows=[]
            for item in hybrid:
                if not item_fields: rows.append(item); continue
                row={}
                if isinstance(item,dict):
                    for key,fs in item_fields.items():
                        path=fs.get('path') or fs.get('selector') if isinstance(fs,dict) else str(fs)
                        if path=='text': value=item.get('name') or item.get('text')
                        elif isinstance(path,str) and path.startswith('@'): value=item.get(path[1:])
                        else:
                            value=item
                            for part in str(path).split('.'): value=value.get(part) if isinstance(value,dict) else None
                        row[key]=clean_text(value) if not isinstance(value,(list,dict)) else value
                rows.append(row)
            return rows or None
    if nodes is None: return None
    item_fields=spec.get('item_fields') or {}; out=[]
    for i in range(min(nodes.count(),500)):
        item=nodes.nth(i); row={}
        for key,fs in item_fields.items():
            try: row[key]=_playwright_item_value(item,fs)
            except Exception: row[key]=None
        if not row: row={'text':clean_text(item.inner_text(timeout=1500))}
        if any(v not in (None,'') for v in row.values()): out.append(row)
    return out or None
'''
s=s[:start]+new2+s[end:]
p.write_text(s,encoding='utf-8')
print('patched codegen')
