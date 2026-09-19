from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from .llm import OpenAICompatibleLLM
from .mcp_client import PlaywrightMCP
from .models import DiscoveryResult, FieldPlan
from .prompts import DISCOVERY_SYSTEM, RECON_SYSTEM, FIELD_SYSTEM, SYNTHESIS_SYSTEM, REPAIR_SYSTEM
from .settings import settings

CHALLENGE_RE = re.compile(
    r"captcha|verify you are human|are you human|access denied|unusual traffic|bot detection|robot check|security check|cloudflare",
    re.I,
)
LOGIN_RE = re.compile(r"sign in|log in|login|ورود|عضویت", re.I)
GENERIC_SELECTOR_RE = re.compile(
    r"^(div|span|p|img|a|body|main|section|article|\.item|\.title|\.content|\.news|#content)$",
    re.I,
)
MAX_VALUE_CHARS = 1200
MAX_EVIDENCE_CHARS = 300


def flatten_template(value: Any, prefix: str = "") -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(child, dict):
                if "value" in child and set(child).issubset({"value", "required", "type", "description", "items"}):
                    raw = child.get("value")
                    typ = child.get("type") or _infer_type(raw)
                    out.append({"name": key, "path": path, "value_type": typ, "required": bool(child.get("required", True)), "description": str(child.get("description", "")), "item_schema": child.get("items") or {}})
                else:
                    out.extend(flatten_template(child, path))
            elif isinstance(child, list):
                out.append({"name": key, "path": path, "value_type": "array", "required": True, "description": "Collection", "item_schema": child[0] if child and isinstance(child[0], dict) else {}})
            else:
                out.append({"name": key, "path": path, "value_type": _infer_type(child), "required": True, "description": ""})
    return out

def _infer_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if value is None:
        return "null"
    return "string"


def set_path(obj: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur = obj
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value



def prepare_page(want_comments: bool = False) -> str:
    click_block = ""
    if want_comments:
        click_block = r"""
      const rx = /(comments?|commentary|replies|show more|load more|view comments|نظرات|دیدگاه|مشاهده نظرات|نمایش نظرات|بیشتر)/i;
      for (let round = 0; round < 3; round++) {
        const els = [...document.querySelectorAll('button, a, [role="button"]')].filter(el => {
          const t = String(el.innerText || el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
          const r = el.getBoundingClientRect();
          const st = getComputedStyle(el);
          return rx.test(t) && r.width > 0 && r.height > 0 && st.display !== 'none' && st.visibility !== 'hidden';
        }).slice(0, 8);
        for (const el of els) { try { el.click(); await sleep(250); } catch {} }
        window.scrollTo(0, document.documentElement.scrollHeight);
        await sleep(300);
      }
"""
    return ("""async () => {
      const sleep = ms => new Promise(r => setTimeout(r, ms));
      let lastHeight = 0, stable = 0;
      for (let i=0; i<24; i++) {
        window.scrollTo(0, document.documentElement.scrollHeight); await sleep(180);
        const h=Math.max(document.body?.scrollHeight||0, document.documentElement.scrollHeight||0);
        if(h===lastHeight) stable++; else stable=0; lastHeight=h;
        if(stable>=2) break;
      }
      %s
      window.scrollTo(0, document.documentElement.scrollHeight); await sleep(250);
      window.scrollTo(0,0); await sleep(150);
      return {height:lastHeight};
    }""") % click_block


def verify_and_extract_js(specs: list[dict[str, Any]]) -> str:
    """Resolve model proposals against the live DOM and emit canonical selectors.

    The LLM is allowed to propose selectors, but those strings are never trusted as
    the final contract.  We execute the proposal, identify the actual DOM node(s),
    then deterministically synthesize stable selectors and candidate selectors.
    This makes repeated analyses converge on the same selector for the same element.
    """
    specs_json = json.dumps(specs, ensure_ascii=False, separators=(",", ":"))
    return f"""() => {{
      const specs = {specs_json};
      const norm = s => String(s ?? '').replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim();
      const visible = el => {{
        if (!el) return false;
        const s = getComputedStyle(el), r = el.getBoundingClientRect();
        return s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity || 1) > 0 && r.width > 0 && r.height > 0;
      }};
      const escapeCss = s => CSS.escape(String(s));
      const stableClass = c => /^[A-Za-z_][A-Za-z0-9_-]{{1,60}}$/.test(c) && !/^(active|selected|show|open|hidden|visible|disabled|loading)$/i.test(c);
      const inRoot = (el, root) => !root || root === document || root.contains(el);
      const scopedCount = (root, selector) => {{
        try {{
          const q = root && root !== document ? root.querySelectorAll(selector) : document.querySelectorAll(selector);
          return q.length;
        }} catch {{ return -1; }}
      }};
      const makePathPart = node => {{
        let part = node.tagName.toLowerCase();
        const classes = [...node.classList].filter(stableClass).slice(0, 3);
        for (const c of classes) part += '.' + escapeCss(c);
        const parent = node.parentElement;
        if (parent) {{
          const same = [...parent.children].filter(x => x.tagName === node.tagName);
          if (same.length > 1) part += `:nth-of-type(${{same.indexOf(node) + 1}})`;
        }}
        return part;
      }};
      const relativeCandidates = (el, root) => {{
        if (!el || el.nodeType !== 1) return [];
        const out = [];
        const push = q => {{
          if (!q || out.includes(q)) return;
          if (scopedCount(root, q) === 1) out.push(q);
        }};
        for (const attr of ['data-testid', 'data-qa', 'data-cy']) {{
          const v = el.getAttribute(attr);
          if (v) push(`${{el.tagName.toLowerCase()}}[${{attr}}="${{String(v).replaceAll('"', '\\\"')}}"]`);
        }}
        if (el.id) push(`#${{escapeCss(el.id)}}`);
        const name = el.getAttribute('name');
        if (name) push(`${{el.tagName.toLowerCase()}}[name="${{String(name).replaceAll('"', '\\\"')}}"]`);
        const classes = [...el.classList].filter(stableClass).slice(0, 3);
        if (classes.length) push(`${{el.tagName.toLowerCase()}}.${{classes.map(escapeCss).join('.')}}`);
        const parts = [];
        let node = el;
        for (let depth = 0; node && depth < 8 && node.nodeType === 1; depth++, node = node.parentElement) {{
          parts.unshift(makePathPart(node));
          const q = parts.join(' > ');
          push(q);
          if (root && root !== document && node === root) break;
        }}
        return out.slice(0, 6);
      }};
      const bestSelector = (el, root) => {{
        const candidates = relativeCandidates(el, root);
        return {{selector: candidates[0] || null, selector_candidates: candidates, unique: !!candidates[0]}};
      }};
      const selectorRoot = scope => {{
        if (!scope) return document;
        try {{ const el = document.querySelector(scope); return el || document; }} catch {{ return document; }}
      }};
      const bodyLike = spec => /body|article body|full text|article text|متن|content/i.test(`${{spec.path || ''}} ${{spec.extraction_hint || ''}}`);
      const cleanCloneText = root => {{
        if (!root) return null;
        const clone = root.cloneNode(true);
        const bad = /(^|[-_\\s])(ad|ads|advert|advertisement|banner|promo|sponsor|sponsored|related|recommend|recommended|comments?|comment-section|share|sharing|social|newsletter|subscribe|cookie|popup|modal|sidebar|breadcrumb|toolbar|navigation)([-_\\s]|$)/i;
        const badText = /(advertisement|تبلیغ|آگهی|مطالب مرتبط|اخبار مرتبط|دیدگاه|نظرات کاربران|اشتراک گذاری|عضویت|خبرهای مرتبط|پیشنهاد سردبیر|مطالب پیشنهادی)/i;
        for (const el of [...clone.querySelectorAll('script,style,noscript,iframe,svg,form')]) el.remove();
        for (const el of [...clone.querySelectorAll('*')]) {{
          const meta = [el.id || '', typeof el.className === 'string' ? el.className : '', el.getAttribute('aria-label') || '', el.getAttribute('role') || '', el.getAttribute('data-ad') || ''].join(' ');
          const t = norm(el.innerText || '');
          const leaf = !el.children.length;
          if (bad.test(meta) || (leaf && t.length < 350 && badText.test(t))) el.remove();
        }}
        const blocks = [...clone.querySelectorAll('p,h2,h3,h4,blockquote,pre,li')]
          .map(x => norm(x.innerText || x.textContent || ''))
          .filter(t => t.length >= 20 && !(t.length < 500 && badText.test(t)));
        const out = [];
        for (const t of blocks) if (!out.length || out[out.length - 1] !== t) out.push(t);
        return norm(out.length ? out.join('\\n\\n') : clone.innerText || '');
      }};
      const jsonldObjects = () => {{
        const out = [];
        for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {{
          try {{ const x = JSON.parse(script.textContent || ''); Array.isArray(x) ? out.push(...x) : out.push(x); }} catch {{}}
        }}
        return out.filter(x => x && typeof x === 'object');
      }};
      const jsonldGet = path => {{
        for (const obj of jsonldObjects()) {{
          let cur = obj;
          for (const part of String(path).split('.')) cur = cur && typeof cur === 'object' ? cur[part] : undefined;
          if (cur !== undefined && cur !== null && cur !== '') return cur;
        }}
        return null;
      }};
      const metaGet = key => {{
        const wanted = String(key).toLowerCase();
        for (const el of document.querySelectorAll('meta[content]')) {{
          const candidate = [el.getAttribute('property'), el.getAttribute('name'), el.getAttribute('itemprop')].filter(Boolean).join(' ').toLowerCase();
          if (candidate === wanted) return norm(el.getAttribute('content') || '');
        }}
        return null;
      }};
      const resolveDomCandidate = (spec, candidate, root) => {{
        const st = spec.selector_type || 'none';
        if (!candidate) return null;
        try {{
          if (st === 'text') {{
            const all = [...root.querySelectorAll('*')].filter(visible);
            const exact = all.filter(el => norm(el.innerText || '') === norm(candidate)).sort((a,b) => (a.innerText || '').length - (b.innerText || '').length);
            return exact[0] || null;
          }}
          if (st === 'xpath') {{
            const context = root === document ? document : root;
            const r = document.evaluate(candidate, context, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
            const nodes = [];
            for (let i = 0; i < r.snapshotLength; i++) if (r.snapshotItem(i)?.nodeType === 1) nodes.push(r.snapshotItem(i));
            return nodes.find(visible) || null;
          }}
          if (st === 'meta') {{
            const direct = root.querySelector(candidate);
            if (direct) return direct;
            return [...root.querySelectorAll('meta[content]')].find(el => [el.getAttribute('property'), el.getAttribute('name'), el.getAttribute('itemprop')].filter(Boolean).join(' ').toLowerCase() === String(candidate).toLowerCase()) || null;
          }}
          if (st === 'css' || st === 'attribute') {{
            const nodes = [...root.querySelectorAll(candidate)].filter(visible);
            if (!nodes.length) return null;
            if (nodes.length === 1) return nodes[0];
            const wanted = norm(spec.value || '');
            if (wanted) {{
              const exact = nodes.find(n => norm(n.innerText || n.textContent || '') === wanted);
              if (exact) return exact;
              const partial = nodes.find(n => norm(n.innerText || n.textContent || '').includes(wanted.slice(0, Math.min(wanted.length, 120))));
              if (partial) return partial;
            }}
            if (bodyLike(spec)) {{
              return nodes.sort((a,b) => (b.querySelectorAll('p').length * 1000 + (b.innerText || '').length) - (a.querySelectorAll('p').length * 1000 + (a.innerText || '').length))[0];
            }}
            return nodes[0];
          }}
        }} catch {{}}
        return null;
      }};
      const readNodeValue = (el, spec) => {{
        if (!el) return null;
        if (bodyLike(spec) && spec.selector_type === 'css') return cleanCloneText(el);
        if (spec.attribute) return norm(el.getAttribute(spec.attribute) || '');
        if (spec.selector_type === 'meta') return norm(el.getAttribute('content') || '');
        if (spec.selector_type === 'attribute') return norm(el.getAttribute(spec.attribute || 'content') || '');
        return norm(el.innerText || el.textContent || '');
      }};
      const relativeFieldSelector = (el, root) => bestSelector(el, root);
      const itemFieldSelectors = (item, root, itemFields) => {{
        const out = {{}};
        for (const [key, fs] of Object.entries(itemFields || {{}})) {{
          const q = fs && typeof fs === 'object' ? fs.selector : fs;
          const attr = fs && typeof fs === 'object' ? fs.attribute : null;
          if (!q || q === 'text' || q === '@text') {{ out[key] = q?.startsWith('@') ? q : 'text'; continue; }}
          if (String(q).startsWith('@')) {{ out[key] = String(q); continue; }}
          let child = null;
          try {{ child = item.querySelector(String(q)); }} catch {{}}
          if (child) out[key] = relativeFieldSelector(child, item).selector || String(q);
          else out[key] = String(q);
        }}
        return out;
      }};
      const extractCollection = spec => {{
        const root = selectorRoot(spec.scope_selector);
        const selectors = [spec.item_selector, spec.selector, ...(spec.selector_candidates || [])].filter(Boolean).filter((v,i,a)=>a.indexOf(v)===i);
        for (const candidate of selectors) {{
          let nodes = [];
          try {{ nodes = [...root.querySelectorAll(candidate)].filter(visible); }} catch {{ nodes = []; }}
          if (nodes.length >= 1) {{
            const first = nodes[0];
            const canonical = bestSelector(first, root);
            const rows = nodes.slice(0,500).map(item => {{
              const row = {{}};
              const fields = spec.item_fields || {{}};
              for (const [key, fs] of Object.entries(fields)) {{
                const q = fs && typeof fs === 'object' ? fs.selector : fs;
                const attr = fs && typeof fs === 'object' ? fs.attribute : null;
                if (!q || q === 'text' || q === '@text') row[key] = norm(item.innerText || item.textContent || '');
                else if (String(q).startsWith('@')) row[key] = norm(item.getAttribute(String(q).slice(1)) || '');
                else {{ let child = null; try {{ child = item.querySelector(String(q)); }} catch {{}}; row[key] = child ? (attr ? norm(child.getAttribute(attr) || '') : norm(child.innerText || child.textContent || '')) : null; }}
              }}
              if (!Object.keys(row).length) row.text = norm(item.innerText || item.textContent || '');
              return row;
            }}).filter(row => Object.values(row).some(v => v !== null && v !== undefined && String(v).trim() !== ''));
            if (rows.length) return {{value: rows, selector: canonical.selector, selector_candidates: canonical.selector_candidates, selector_unique: true, item_fields: itemFieldSelectors(first, first, spec.item_fields || {{}}), match_count: nodes.length}};
          }}
          // Hybrid JSON-LD candidate: script[...] path field.
          const m = String(candidate).match(/^(.*?)\\s+path\\s+(.+)$/i);
          if (m) {{
            const path = m[2].trim();
            const raw = jsonldGet(path);
            if (Array.isArray(raw) && raw.length) return {{value: raw, selector: path, selector_candidates: [path], selector_unique: true, item_fields: spec.item_fields || {{}}, match_count: raw.length}};
          }}
        }}
        return null;
      }};
      const result = {{}};
      for (const spec of specs) {{
        const path = String(spec.path || '');
        let value = null, selector = null, selector_candidates = [], selector_unique = false, item_fields = spec.item_fields || {{}}, match_count = 0;
        if (spec.value_type === 'array' || spec.item_selector || spec.item_fields) {{
          const coll = extractCollection(spec);
          if (coll) {{ value = coll.value; selector = coll.selector; selector_candidates = coll.selector_candidates || []; selector_unique = !!coll.selector_unique; match_count = coll.match_count || 0; item_fields = coll.item_fields || item_fields; }}
        }} else if (spec.selector_type === 'jsonld') {{
          for (const candidate of [spec.selector, ...(spec.selector_candidates || [])].filter(Boolean)) {{
            const raw = jsonldGet(candidate);
            if (raw !== null && raw !== undefined && raw !== '') {{ value = raw; selector = candidate; selector_candidates = [candidate, ...(spec.selector_candidates || [])].filter((v,i,a)=>v && a.indexOf(v)===i).slice(0,6); selector_unique = true; break; }}
          }}
        }} else {{
          for (const candidate of [spec.selector, ...(spec.selector_candidates || [])].filter(Boolean).filter((v,i,a)=>a.indexOf(v)===i)) {{
            const el = resolveDomCandidate(spec, candidate, root);
            if (!el) continue;
            value = readNodeValue(el, spec);
            if (value === null || value === '') continue;
            const canonical = bestSelector(el, root);
            selector = canonical.selector || candidate;
            selector_candidates = canonical.selector_candidates.length ? canonical.selector_candidates : [candidate];
            selector_unique = !!canonical.selector;
            match_count = scopedCount(root, candidate);
            break;
          }}
        }}
        // Semantic fallback runs only after ALL model-proposed selectors have failed.
        if (value === null || value === '') {{
          const low = path.toLowerCase();
          let el = null;
          if (/^title$|headline|عنوان/.test(low)) el = root.querySelector('h1');
          else if (/author|byline|نویسنده/.test(low)) el = root.querySelector('[rel="author"],[itemprop="author"],.author,.byline,[class*="author"]');
          else if (/date|published|publish|تاریخ/.test(low)) {{ const m=[...document.querySelectorAll('meta[content]')].find(x=>/published_time|date|pubdate/i.test([x.getAttribute('property'),x.getAttribute('name'),x.getAttribute('itemprop')].filter(Boolean).join(' '))); if(m) {{ value=norm(m.getAttribute('content')||''); selector=`meta[${{m.getAttribute('property')?'property':'name'}}="${{m.getAttribute('property')||m.getAttribute('name')}}"]`; selector_candidates=[selector]; selector_unique=true; }} }}
          else if (/image|photo|thumbnail|picture|تصویر/.test(low)) {{ const m=document.querySelector('meta[property="og:image"],meta[name="twitter:image"]'); if(m) {{ value=norm(m.getAttribute('content')||''); selector=`${{m.getAttribute('property')?'meta[property="'+m.getAttribute('property')+'"]':'meta[name="'+m.getAttribute('name')+'"]'}}`; selector_candidates=[selector]; selector_unique=true; }} }}
          else if (/body|content|متن|full text/.test(low)) el=[...document.querySelectorAll('[itemprop="articleBody"],[data-article-body],[class*="article-body"],[class*="article-content"],article,main')].sort((a,b)=>(b.querySelectorAll('p').length*1000+(b.innerText||'').length)-(a.querySelectorAll('p').length*1000+(a.innerText||'').length))[0];
          if (el && value === null) {{ value = readNodeValue(el, {{...spec, selector_type:'css'}}); const canonical=bestSelector(el,root); selector=canonical.selector; selector_candidates=canonical.selector_candidates; selector_unique=!!canonical.selector; match_count=1; }}
        }}
        result[path]={{value, selector, selector_candidates, selector_unique, scope_selector:scopeCanonical, item_selector, item_fields, match_count, canonicalized:true}};
      }}
      return result;
    }}"""


def compact_browser_evidence() -> str:
    return r"""() => {
      const clean = (s, n=700) => String(s || '').replace(/\s+/g,' ').trim().slice(0,n);
      const esc = (s) => CSS.escape(String(s));
      const stableSelector = (el) => {
        if (!el || el.nodeType !== 1) return null;
        if (el.id) return `#${esc(el.id)}`;
        for (const a of ['data-testid','data-qa','data-cy','name']) {
          const v = el.getAttribute(a);
          if (v) return `${el.tagName.toLowerCase()}[${a}="${String(v).replaceAll('"','\\"')}"]`;
        }
        const parts = [];
        let node = el;
        for (let depth=0; node && depth<5 && node.nodeType===1; depth++, node=node.parentElement) {
          let p = node.tagName.toLowerCase();
          const classes = [...node.classList].filter(c => c && c.length < 60).slice(0,3);
          if (classes.length) p += '.' + classes.map(esc).join('.');
          const siblings = node.parentElement ? [...node.parentElement.children].filter(x => x.tagName===node.tagName) : [];
          if (siblings.length > 1) p += `:nth-of-type(${siblings.indexOf(node)+1})`;
          parts.unshift(p);
          const candidate = parts.join(' > ');
          try { if (document.querySelectorAll(candidate).length === 1) return candidate; } catch {}
        }
        return parts.join(' > ');
      };
      const nodeInfo = (el) => ({
        selector: stableSelector(el),
        tag: el.tagName.toLowerCase(),
        id: el.id || null,
        cls: typeof el.className === 'string' ? clean(el.className,220) : null,
        text: clean(el.innerText,900),
        text_len: clean(el.innerText,100000).length,
        count: (()=>{ try { return document.querySelectorAll(stableSelector(el) || '___never___').length } catch { return -1 } })(),
      });

      const meta = [...document.querySelectorAll('meta[content]')]
        .map(m => ({name:m.getAttribute('name'), property:m.getAttribute('property'), itemprop:m.getAttribute('itemprop'), content:clean(m.content,500)}))
        .filter(x => /title|description|author|date|time|published|modified|image|canonical|og:|twitter:/i.test([x.name,x.property,x.itemprop].filter(Boolean).join(' ')))
        .slice(0,80);

      const jsonld = [...document.querySelectorAll('script[type="application/ld+json"]')]
        .map(x => clean(x.textContent,5000)).filter(Boolean).slice(0,8);

      const articleCandidates = [...document.querySelectorAll('article, [role="article"], main, [itemprop="articleBody"], [class*="article"], [class*="content"], [class*="story"], [class*="post"]')]
        .map(nodeInfo)
        .filter(x => x.text_len >= 120)
        .sort((a,b) => b.text_len - a.text_len)
        .slice(0,15);

      const headingCandidates = [...document.querySelectorAll('h1,h2,h3,[itemprop="headline"]')]
        .map(nodeInfo).filter(x => x.text).slice(0,30);

      const collections = [...document.querySelectorAll('[class*="comment" i], [id*="comment" i], [class*="reply" i], [id*="reply" i], [data-comment], [data-reply]')]
        .map(node => {
          const children=[...node.children].filter(x => String(x.innerText||'').trim().length >= 8).slice(0,20).map(x => ({selector:stableSelector(x), text:clean(x.innerText,260)}));
          return {...nodeInfo(node), children};
        })
        .filter(x => x.children.length >= 2 || /comment|reply/i.test(`${x.id||''} ${x.cls||''}`))
        .sort((a,b)=>(b.children.length*10000+b.text_len)-(a.children.length*10000+a.text_len))
        .slice(0,20);

      const images = [...document.images].map(img => ({
        selector: stableSelector(img), src: img.currentSrc || img.src || img.getAttribute('data-src'),
        alt: clean(img.alt,300), width: img.naturalWidth || null, height: img.naturalHeight || null,
        cls: typeof img.className === 'string' ? clean(img.className,160) : null
      })).filter(x => x.src).slice(0,40);

      const links = [...document.querySelectorAll('a[href]')]
        .map(a => ({href:a.href, text:clean(a.innerText,180), cls: typeof a.className === 'string' ? clean(a.className,160) : null}))
        .filter(x => x.text || x.href).slice(0,60);

      return JSON.stringify({
        page_title: clean(document.title,300),
        url: location.href,
        collections,
        body_text_sample: clean(document.body?.innerText,4000),
        meta, jsonld, headings: headingCandidates, articles: articleCandidates, images, links,
      });
    }"""


class ExtractorAgent:
    def __init__(self) -> None:
        self.browser = PlaywrightMCP()
        self.llm = OpenAICompatibleLLM()
        self.last_artifacts: dict[str, Any] = {}

    @staticmethod
    def _mcp_tool_def(tool: Any) -> dict[str, Any]:
        name = getattr(tool, "name", "")
        description = getattr(tool, "description", None) or ""
        schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None) or {"type": "object", "properties": {}}
        return {
            "type": "function",
            "function": {
                "name": str(name),
                "description": str(description)[:2000],
                "parameters": schema,
            },
        }

    @staticmethod
    def _tool_is_allowed(name: str) -> bool:
        blocked = {
            "browser_navigate",
            "browser_navigate_back",
            "browser_go_back",
            "browser_go_forward",
            "browser_reload",
            "browser_close",
        }
        return name not in blocked

    @staticmethod
    def _validate_analysis(analysis: Any, contract: list[dict[str, Any]]) -> tuple[list[FieldPlan], list[str], dict[str, Any]]:
        """Normalize the LLM response against the authoritative template contract.

        The model may return extra/malformed fields, omit fields, or return values with
        inconsistent shapes. This method makes the final DiscoveryResult deterministic:
        every contract leaf is represented, missing values become null, and required
        fields are reported in ``missing_required``.
        """
        if not isinstance(analysis, dict):
            analysis = {}

        raw_fields = analysis.get("fields")
        if not isinstance(raw_fields, list):
            raw_fields = []

        by_path: dict[str, dict[str, Any]] = {}
        for item in raw_fields:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip()
            if path:
                by_path[path] = item

        raw_proposed = analysis.get("proposed_json")
        proposed: dict[str, Any] = dict(raw_proposed) if isinstance(raw_proposed, dict) else {}
        fields: list[FieldPlan] = []
        missing: list[str] = []

        def nonempty(value: Any) -> bool:
            if value is None:
                return False
            if isinstance(value, str):
                return bool(value.strip())
            if isinstance(value, (list, dict)):
                return bool(value)
            return True

        for spec in contract:
            path = str(spec.get("path") or "")
            item = by_path.get(path, {})
            value_type = str(spec.get("value_type") or "string")
            found = bool(item.get("found")) and nonempty(item.get("value"))
            value = item.get("value") if found else None

            # Preserve the model's proposed value only when the field itself has no
            # explicit field-level value but the response includes it in proposed_json.
            if not found:
                cursor: Any = proposed
                for part in path.split("."):
                    if not isinstance(cursor, dict) or part not in cursor:
                        cursor = None
                        break
                    cursor = cursor[part]
                if nonempty(cursor):
                    value = cursor
                    found = True

            selector = item.get("selector")
            if selector is not None:
                selector = str(selector).strip() or None
            if selector and GENERIC_SELECTOR_RE.fullmatch(selector.strip()):
                selector = None
                found = False if value is None else found

            selector_candidates = item.get("selector_candidates")
            if not isinstance(selector_candidates, list):
                selector_candidates = []
            selector_candidates = [str(x).strip() for x in selector_candidates if str(x).strip()][:4]

            item_fields = item.get("item_fields")
            if not isinstance(item_fields, dict):
                item_fields = {}

            fp = FieldPlan(
                name=str(spec.get("name") or path.split(".")[-1]),
                path=path,
                description=str(spec.get("description") or ""),
                required=bool(spec.get("required", True)),
                value_type=value_type if value_type in {"string", "number", "boolean", "array", "object", "null"} else "string",
                selector=selector,
                selector_type=item.get("selector_type") if item.get("selector_type") in {"css", "xpath", "meta", "jsonld", "attribute", "text", "none"} else "none",
                attribute=(str(item.get("attribute")) if item.get("attribute") else None),
                selector_candidates=selector_candidates,
                scope_selector=(str(item.get("scope_selector")) if item.get("scope_selector") else None),
                extraction_hint=str(item.get("extraction_hint") or "")[:800],
                found=found,
                value=value,
                evidence=(str(item.get("evidence"))[:MAX_EVIDENCE_CHARS] if item.get("evidence") else None),
                confidence=max(0.0, min(float(item.get("confidence", 0.0) or 0.0), 1.0)),
                item_selector=(str(item.get("item_selector")) if item.get("item_selector") else None),
                item_fields=item_fields,
                collection_count=(int(item.get("collection_count")) if isinstance(item.get("collection_count"), (int, float)) else None),
            )
            fields.append(fp)
            set_path(proposed, path, value)
            if fp.required and not found:
                missing.append(path)

        # A field must not claim READY if the model omitted fields entirely.
        return fields, missing, proposed

    @staticmethod
    def _coerce_mcp_payload(payload: Any) -> Any:
        if isinstance(payload, dict):
            for key in ("structuredContent", "structured_content", "result", "data"):
                value = payload.get(key)
                if value is not None:
                    return ExtractorAgent._coerce_mcp_payload(value)
            return payload
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict) and item.get("text") is not None:
                    return ExtractorAgent._coerce_mcp_payload(item["text"])
            return payload
        if isinstance(payload, str):
            text=payload.strip()
            try:
                return json.loads(text)
            except Exception:
                return text
        return payload

    @staticmethod
    def _coerce_source(payload: Any) -> str:
        value = ExtractorAgent._coerce_mcp_payload(payload)
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _limit_llm_source(source_html: str) -> str:
        limit = settings.llm_source_max_chars
        if limit and len(source_html) > limit:
            raise RuntimeError(
                f"Rendered HTML is {len(source_html):,} characters, above LLM_SOURCE_MAX_CHARS={limit:,}. "
                "Increase LLM_SOURCE_MAX_CHARS for a long-context model or use a larger-context deployment."
            )
        return source_html

    @staticmethod
    def _merge_verification(fields: list[FieldPlan], verified: Any) -> tuple[list[FieldPlan], list[str], dict[str, Any]]:
        if not isinstance(verified, dict):
            proposed={}
            missing=[f.path for f in fields if f.required and not f.found]
            for f in fields:
                set_path(proposed, f.path, f.value)
            return fields, missing, proposed
        proposed={}
        by_path=verified
        for field in fields:
            raw=by_path.get(field.path)
            if isinstance(raw, dict):
                # Once browser verification returns a record, it becomes the source of truth.
                # Do not keep an LLM-only value when the live page could not produce it.
                value=raw.get("value")
                selector=raw.get("selector")
                unique=raw.get("selector_unique")
                field.value=value if value not in (None, "", []) else None
                field.found=field.value not in (None, "", [])
                if selector:
                    # The verified/canonical selector is the executable contract.
                    # It is derived from the actual DOM node, not copied blindly from the LLM.
                    field.selector=str(selector)
                verified_candidates = raw.get("selector_candidates")
                if isinstance(verified_candidates, list):
                    field.selector_candidates=[str(x) for x in verified_candidates if str(x).strip()][:6]
                verified_scope = raw.get("scope_selector")
                if verified_scope:
                    field.scope_selector=str(verified_scope)
                verified_item_selector = raw.get("item_selector")
                if verified_item_selector:
                    field.item_selector=str(verified_item_selector)
                verified_item_fields = raw.get("item_fields")
                if isinstance(verified_item_fields, dict):
                    field.item_fields=verified_item_fields
                match_count = raw.get("match_count")
                if isinstance(match_count, (int, float)):
                    field.evidence=(field.evidence or "") + f" | verified_matches={int(match_count)}"
                if unique is False and field.selector:
                    field.confidence=min(field.confidence, 0.65)
            set_path(proposed, field.path, field.value)
        missing=[f.path for f in fields if f.required and not f.found]
        return fields, missing, proposed

    async def _emit(self, cb: Callable[[str, str, dict[str, Any] | None], Awaitable[None]] | None, stage: str, message: str, meta: dict[str, Any] | None = None) -> None:
        if cb is not None:
            await cb(stage, message, meta or {})

    async def _get_browser_tool_defs(self) -> list[dict[str, Any]]:
        tools = await self.browser.list_tools()
        allowed = {"browser_snapshot", "browser_evaluate"}
        selected = [t for t in tools if str(getattr(t, "name", "")) in allowed]
        return [self._mcp_tool_def(t) for t in selected]

    async def _run_tool_agent(
        self,
        *,
        system: str,
        user: str,
        tools: list[dict[str, Any]],
        progress_cb: Callable[[str, str, dict[str, Any] | None], Awaitable[None]] | None,
        stage: str,
        max_rounds: int,
        max_output_chars: int = 8000,
    ) -> dict[str, Any]:
        async def executor(name: str, args: dict[str, Any]) -> Any:
            if name not in {"browser_snapshot", "browser_evaluate"}:
                raise RuntimeError(f"Tool not allowed in analysis phase: {name}")
            return await self.browser.call(name, args)

        async def on_event(kind: str, data: dict[str, Any]) -> None:
            if kind == "round_start":
                await self._emit(progress_cb, stage, f"LLM round {data['round']}/{data['max_rounds']}")
            elif kind == "tool_call":
                await self._emit(progress_cb, stage, f"Tool call: {data['name']}", {"round": data.get("round"), "arguments": data.get("arguments")})
            elif kind == "tool_result":
                await self._emit(progress_cb, stage, f"Tool result received: {data['name']}", {"round": data.get("round")})
            elif kind == "final_response":
                await self._emit(progress_cb, stage, "LLM returned the round result.")

        return await self.llm.chat_with_tools(
            system=system,
            user=user,
            tools=tools,
            executor=executor,
            max_rounds=max_rounds,
            max_tool_output_chars=max_output_chars,
            max_repeated_tool_calls=1,
            round_delay_seconds=0.35,
            on_event=on_event,
        )

    async def _inspect_with_mcp(
        self,
        url: str,
        template: dict[str, Any],
        contract: list[dict[str, Any]],
        progress_cb: Callable[[str, str, dict[str, Any] | None], Awaitable[None]] | None = None,
    ) -> DiscoveryResult:
        host = urlparse(url).netloc.lower().split(":", 1)[0]
        self.last_artifacts = {}

        await self._emit(progress_cb, "browser_prepare", "Playwright MCP session is ready on the requested page.")

        scroll_js = r"""async () => {
          const sleep = ms => new Promise(r => setTimeout(r, ms));
          let lastHeight = 0, stable = 0;
          for (let i = 0; i < 28; i++) {
            window.scrollBy({top: Math.max(360, Math.floor(window.innerHeight * 0.85)), behavior: 'auto'});
            await sleep(220);
            const h = Math.max(document.body?.scrollHeight || 0, document.documentElement?.scrollHeight || 0);
            if (h === lastHeight) stable++; else stable = 0;
            lastHeight = h;
            if (stable >= 3) break;
          }
          window.scrollTo({top: 0, behavior: 'auto'});
          await sleep(300);
          return {height:lastHeight, url:location.href, title:document.title};
        }"""
        await self._emit(progress_cb, "scroll", "Scrolling through the full rendered page to trigger lazy-loaded content.")
        try:
            scroll_result = self._coerce_mcp_payload(await self.browser.call("browser_evaluate", {"function": scroll_js}))
        except Exception as exc:
            raise RuntimeError(f"Playwright MCP scroll failed: {exc}") from exc
        await self._emit(progress_cb, "scroll", "Page scroll completed; browser returned to the top.", scroll_result if isinstance(scroll_result, dict) else {})

        await self._emit(progress_cb, "recon", "Starting round 1: page reconnaissance.")
        tool_defs = await self._get_browser_tool_defs()
        if not tool_defs:
            raise RuntimeError("Playwright MCP did not expose browser_snapshot/browser_evaluate tools")
        recon_user = f"""
CURRENT URL: {url}
AUTHORITATIVE TEMPLATE:
{json.dumps(template, ensure_ascii=False, separators=(',', ':'))}
LEAF CONTRACT:
{json.dumps(contract, ensure_ascii=False, separators=(',', ':'))}

Inspect the already-open page. Build a compact map of the page structure, metadata sources,
article/content regions, repeated collections, JSON-LD, and any special behavior relevant to the template.
Use the browser tools sparingly: one snapshot if useful and only targeted evaluate calls. Never navigate.
"""
        recon = await self._run_tool_agent(
            system=RECON_SYSTEM,
            user=recon_user,
            tools=tool_defs,
            progress_cb=progress_cb,
            stage="recon",
            max_rounds=2,
            max_output_chars=8000,
        )
        self.last_artifacts["round1_recon_prompt"] = recon_user
        self.last_artifacts["round1_recon"] = recon

        await self._emit(progress_cb, "investigation", "Starting round 2: field-by-field investigation.")
        field_user = f"""
CURRENT URL: {url}
AUTHORITATIVE TEMPLATE:
{json.dumps(template, ensure_ascii=False, separators=(',', ':'))}
LEAF CONTRACT:
{json.dumps(contract, ensure_ascii=False, separators=(',', ':'))}

RECONNAISSANCE RESULT:
{json.dumps(recon, ensure_ascii=False, separators=(',', ':'))}

Investigate every requested field on the CURRENT page. Use targeted browser_snapshot/browser_evaluate calls only
when necessary. Prioritize exact DOM evidence, JSON-LD/meta evidence, collection cardinality, and robust selector candidates.
Do not echo long content. Never navigate to another URL.
"""
        investigation = await self._run_tool_agent(
            system=FIELD_SYSTEM,
            user=field_user,
            tools=tool_defs,
            progress_cb=progress_cb,
            stage="investigation",
            max_rounds=3,
            max_output_chars=9000,
        )
        self.last_artifacts["round2_field_prompt"] = field_user
        self.last_artifacts["round2_investigation"] = investigation

        await self._emit(progress_cb, "source_capture", "Capturing the full post-render HTML for artifacts and deterministic verification.")
        try:
            source_raw = await self.browser.call("browser_evaluate", {"function": "() => document.documentElement.outerHTML"})
            source_html = self._coerce_source(source_raw)
        except Exception as exc:
            raise RuntimeError(f"Playwright MCP source capture failed: {exc}") from exc
        if not source_html.strip():
            raise RuntimeError("Playwright returned an empty rendered HTML source")
        self._limit_llm_source(source_html) if settings.llm_source_max_chars else None

        try:
            page_info = self._coerce_mcp_payload(await self.browser.call("browser_evaluate", {"function": "() => ({title:document.title,url:location.href})"}))
        except Exception:
            page_info = {}
        page_title = str(page_info.get("title") or "") if isinstance(page_info, dict) else ""

        await self._emit(progress_cb, "synthesis", "Starting round 4: critic + final structure synthesis.")
        synthesis_user = f"""
CURRENT URL: {url}
AUTHORITATIVE TEMPLATE:
{json.dumps(template, ensure_ascii=False, separators=(',', ':'))}
LEAF CONTRACT:
{json.dumps(contract, ensure_ascii=False, separators=(',', ':'))}

ROUND 1 — RECON:
{json.dumps(recon, ensure_ascii=False, separators=(',', ':'))}

ROUND 2 — FIELD INVESTIGATION:
{json.dumps(investigation, ensure_ascii=False, separators=(',', ':'))}

Create the final Extraction Structure. Reconcile evidence conservatively. Every requested field must appear exactly once.
Do not copy large content. For each field provide the best primary selector and robust candidates. Collections must have
item_selector/item_fields. Mark unsupported fields found=false/null.
Return ONLY JSON.
"""
        synthesis = await self.llm.chat_json(SYNTHESIS_SYSTEM, synthesis_user, max_tokens=settings.llm_max_tokens)
        self.last_artifacts["round4_synthesis_prompt"] = synthesis_user
        self.last_artifacts["round4_synthesis"] = synthesis
        fields, missing, proposed = self._validate_analysis(synthesis, contract)

        await self._emit(progress_cb, "verification", "Verifying every proposed selector against the live browser DOM.")
        verify_specs = [f.model_dump() for f in fields]
        verified = {}
        try:
            raw_verified = await self.browser.call("browser_evaluate", {"function": verify_and_extract_js(verify_specs)})
            verified = self._coerce_mcp_payload(raw_verified)
            fields, missing, proposed = self._merge_verification(fields, verified)
        except Exception as exc:
            self.last_artifacts["verification_error"] = str(exc)
            await self._emit(progress_cb, "verification", f"Verification warning: {exc}", level="warning")

        # Optional repair round: only target fields that are still missing or have weak verification.
        repair_targets = [f for f in fields if f.required and (not f.found or not f.selector or f.confidence < 0.78)]
        if repair_targets:
            await self._emit(progress_cb, "repair", f"Starting optional repair round for {len(repair_targets)} weak field(s).")
            repair_contract = [f.model_dump() for f in repair_targets]
            repair_user = f"""
CURRENT URL: {url}
REPAIR TARGETS:
{json.dumps(repair_contract, ensure_ascii=False, separators=(',', ':'))}
PREVIOUS SYNTHESIS:
{json.dumps(synthesis, ensure_ascii=False, separators=(',', ':'))}
VERIFICATION RESULT:
{json.dumps(verified, ensure_ascii=False, separators=(',', ':'))}

Inspect ONLY these weak fields on the current page. Prefer one or two targeted browser_evaluate calls.
Return corrected field records only. Do not navigate.
"""
            repair = await self._run_tool_agent(
                system=REPAIR_SYSTEM,
                user=repair_user,
                tools=tool_defs,
                progress_cb=progress_cb,
                stage="repair",
                max_rounds=2,
                max_output_chars=8000,
            )
            self.last_artifacts["round5_repair_prompt"] = repair_user
            self.last_artifacts["round5_repair"] = repair
            repair_fields = repair.get("fields") if isinstance(repair, dict) else None
            if isinstance(repair_fields, list):
                merged = {f.path: f for f in fields}
                for item in repair_fields:
                    if not isinstance(item, dict) or not item.get("path"):
                        continue
                    path = str(item["path"])
                    old = merged.get(path)
                    if old is None:
                        continue
                    candidate = FieldPlan.model_validate({**old.model_dump(), **item})
                    merged[path] = candidate
                fields = [merged[f.path] for f in fields]
                verified = self._coerce_mcp_payload(await self.browser.call("browser_evaluate", {"function": verify_and_extract_js([f.model_dump() for f in fields])}))
                fields, missing, proposed = self._merge_verification(fields, verified)
        else:
            await self._emit(progress_cb, "repair", "No repair round needed; verified structure is strong.")

        anti_bot_state = str(synthesis.get("anti_bot_state") or recon.get("anti_bot_state") or "NONE")
        status = "INCOMPLETE" if missing else "READY"
        if anti_bot_state in {"CHALLENGE", "LOGIN", "BLOCKED"}:
            status = "NEEDS_HUMAN"

        self.last_artifacts.update({
            "source_html": source_html,
            "verification": verified,
            "final_structure": {
                "status": status,
                "host": host,
                "url": url,
                "page_title": page_title,
                "anti_bot_state": anti_bot_state,
                "template": template,
                "proposed_json": proposed,
                "fields": [f.model_dump() for f in fields],
                "missing_required": missing,
                "method": "playwright",
            },
            "analysis_mode": "multi_round_playwright",
        })

        await self._emit(progress_cb, "finalize", f"Analysis complete: {status}. {len(fields) - len(missing)}/{len(fields)} fields verified.")
        return DiscoveryResult(
            status=status,
            host=host,
            url=url,
            page_title=page_title or str(synthesis.get("page_title") or recon.get("page_title") or ""),
            anti_bot_state=anti_bot_state,
            method="playwright",
            method_reason="Playwright-only multi-round analysis with bounded MCP tool calls, targeted field investigation, deterministic verification, and optional repair.",
            source="playwright",
            template=template,
            proposed_json=proposed,
            fields=fields,
            missing_required=missing,
            evidence=[str(x)[:500] for x in (synthesis.get("evidence") or []) if x][:12],
            crawler_notes=[
                "Round 1: page reconnaissance.",
                "Round 2: field-by-field targeted browser investigation.",
                "Round 3: deterministic Playwright verification.",
                "Round 4: critic/synthesis.",
                "Round 5: repair only when weak fields remained.",
            ] + [str(x)[:500] for x in (synthesis.get("crawler_notes") or []) if x],
            trace=[
                "Playwright MCP only; no static preflight and no routing LLM.",
                "Tool calls were limited to browser_snapshot/browser_evaluate and bounded per phase.",
                "Full rendered HTML was captured for artifacts but was not sent wholesale to the LLM.",
                "The LLM saw targeted browser evidence through its tool calls, then deterministic verification checked the final selectors.",
            ],
        )

    async def open_page(self, url: str) -> None:
        await self.browser.call("browser_navigate", {"url": url})

    async def inspect(self, url: str, template: dict[str, Any], navigate: bool = True, progress_cb: Callable[[str, str, dict[str, Any] | None], Awaitable[None]] | None = None) -> DiscoveryResult:
        contract=flatten_template(template)
        if navigate:
            await self.open_page(url)
        return await self._inspect_with_mcp(url, template, contract, progress_cb=progress_cb)
