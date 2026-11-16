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

DIRECT_TOOL_SYSTEM = r"""
You are a senior web-forensics and extraction engineer.
You are analyzing the CURRENT page already open in the Playwright browser.
The JSON template is the authoritative contract. Your job is to discover exact executable extraction rules for every requested field.

HIGHEST PRIORITY: JSON-LD FIRST
Before investigating visible DOM selectors for any field, completely inspect and parse ALL
script[type="application/ld+json"] blocks on the current page.
- Inspect every JSON-LD script, including arrays and @graph objects.
- Resolve nested paths such as author.name, image.url, publisher.name and mainEntity.url.
- For each requested field, first determine whether JSON-LD contains the exact requested value.
- Only after the JSON-LD pass is complete should you inspect meta tags and visible DOM for fields
  absent, incomplete, or semantically different in JSON-LD.
- When JSON-LD supplies the requested value, selector_type MUST be "jsonld" and selector MUST be
  only the executable property path, e.g. "headline" or "author.name".
- NEVER return explanatory selector strings such as
  "script[type=application/ld+json] (content.headline via JSON.parse)".
- JSON-LD selector candidates must likewise be executable property paths, never prose.

ALLOWED BEHAVIOR
- Use browser_snapshot and browser_evaluate to inspect the current page.
- You may click ONLY same-page controls that clearly expand comments, replies, load more, show more, or disclosures.
- Never navigate, reload, go back, go forward, or open another page.

WORK IN THIS ORDER
1. Complete the JSON-LD pass.
2. Map every template field to an exact JSON-LD path whenever possible and verify its returned value.
3. For fields not satisfied by JSON-LD, inspect meta tags, then targeted visible DOM.
4. For every scalar field, return an exact primary extraction rule plus up to 6 executable alternatives
   that resolve to the SAME value.
5. For body/full_text, identify the smallest stable container containing the COMPLETE article and excluding
   ads, related content, comments, sharing, navigation, footer, newsletter and cookie/popup content.
6. For collections, return the repeated item selector plus relative item_fields. Preserve authoritative JSON-LD arrays/objects.
7. Verify rules with browser_evaluate before finalizing; check cardinality and representative value.
8. Never use a semantic fallback merely because an exact rule failed. Return found=false when unverified.

ANTI-HALLUCINATION
- found=true ONLY after browser confirmation.
- Never invent selectors or values from website conventions.
- Generic selectors such as div/article/main/.content are invalid unless uniquely scoped and verified.
- selector_candidates must all be executable rules, never explanations.

RETURN ONLY JSON:
{
  "page_title":"",
  "anti_bot_state":"NONE|CHALLENGE|LOGIN|CONSENT|BLOCKED|UNKNOWN",
  "proposed_json":{},
  "fields":[
    {
      "path":"",
      "value_type":"string|number|boolean|array|object|null",
      "selector":"...",
      "selector_type":"css|xpath|meta|jsonld|attribute|text|none",
      "attribute":null,
      "selector_candidates":[],
      "scope_selector":null,
      "extraction_hint":"",
      "found":false,
      "value":null,
      "evidence":"",
      "confidence":0.0,
      "item_selector":null,
      "item_fields":{},
      "collection_count":null
    }
  ],
  "evidence":[],
  "crawler_notes":[]
}
"""

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


def verify_exact_selectors_js(specs: list[dict[str, Any]]) -> str:
    """Verify ONLY the selectors/candidates proposed by the agent.

    No semantic guessing is performed here. A field that cannot be reproduced from its
    proposed extraction rule is treated as unverified and must go through repair.
    """
    specs_json = json.dumps(specs, ensure_ascii=False, separators=(",", ":"))
    return f"""() => {{
      const specs = {specs_json};
      const norm = s => String(s ?? '').replace(/\\u00a0/g,' ').replace(/\\s+/g,' ').trim();
      const visible = el => {{ if (!el) return false; const s=getComputedStyle(el), r=el.getBoundingClientRect(); return s.display!=='none'&&s.visibility!=='hidden'&&Number(s.opacity||1)>0&&r.width>0&&r.height>0; }};
      const scopeRoot = scope => {{ if(!scope) return document; try{{return document.querySelector(scope)||document}}catch{{return document}} }};
      const stableClass = c => /^[A-Za-z_][A-Za-z0-9_-]{{1,60}}$/.test(c) && !/^(active|selected|show|open|hidden|visible|disabled|loading)$/i.test(c);
      const uniqueSelector = (el, root) => {{
        if(!el) return null; root = root || document; const count=q=>{{try{{return (root===document?document:root).querySelectorAll(q).length}}catch{{return -1}}}};
        const esc=s=>CSS.escape(String(s)); const push=[];
        const add=q=>{{if(q&&!push.includes(q)&&count(q)===1)push.push(q)}};
        for(const a of ['data-testid','data-qa','data-cy']){{const v=el.getAttribute(a);if(v)add(`${{el.tagName.toLowerCase()}}[${{a}}="${{String(v).replaceAll('"','\\"')}}"]`)}}
        if(el.id)add('#'+esc(el.id)); const name=el.getAttribute('name'); if(name)add(`${{el.tagName.toLowerCase()}}[name="${{String(name).replaceAll('"','\\"')}}"]`);
        const cls=[...el.classList].filter(stableClass).slice(0,3); if(cls.length)add(`${{el.tagName.toLowerCase()}}.${{cls.map(esc).join('.')}}`);
        const parts=[]; let n=el;
        for(let d=0;n&&d<8&&n.nodeType===1;d++,n=n.parentElement){{let part=n.tagName.toLowerCase();const cs=[...n.classList].filter(stableClass).slice(0,3);for(const c of cs)part+='.'+esc(c);const same=n.parentElement?[...n.parentElement.children].filter(x=>x.tagName===n.tagName):[];if(same.length>1)part+=`:nth-of-type(${{same.indexOf(n)+1}})`;parts.unshift(part);add(parts.join(' > '));}}
        return push.slice(0,6);
      }};
      const jsonldObjects=()=>{{const out=[];const add=x=>{{if(Array.isArray(x)){{for(const item of x)add(item);return}}if(!x||typeof x!=='object')return;out.push(x);if(x['@graph'])add(x['@graph'])}};for(const s of document.querySelectorAll('script[type="application/ld+json"]')){{try{{add(JSON.parse(s.textContent||''))}}catch{{}}}}return out}};
      const jsonldGet=path=>{{for(const obj of jsonldObjects()){{let cur=obj;for(const p of String(path).split('.'))cur=(cur&&typeof cur==='object')?cur[p]:undefined;if(cur!==undefined&&cur!==null&&cur!=='')return cur}}return null}};
      const textValue=(root,text)=>{{if(!text)return null;const wanted=norm(text);const nodes=[...(root||document).querySelectorAll('*')].filter(visible).sort((a,b)=>(a.innerText||'').length-(b.innerText||'').length);const ex=nodes.find(e=>norm(e.innerText||'')===wanted);if(ex)return norm(ex.innerText||'');return null}};
      const readNode=(el,spec)=>{{if(!el)return null;if(spec.attribute)return norm(el.getAttribute(spec.attribute)||'');if(spec.selector_type==='meta')return norm(el.getAttribute('content')||'');if(/body|full text|article body|متن|content/i.test(`${{spec.path||''}} ${{spec.extraction_hint||''}}`)){{const clone=el.cloneNode(true);for(const x of [...clone.querySelectorAll('script,style,noscript,iframe,svg,form')])x.remove();return norm(clone.innerText||'')}}return norm(el.innerText||el.textContent||'')}};
      const itemValue=(item,fs)=>{{const q=fs&&typeof fs==='object'?fs.selector:fs;const attr=fs&&typeof fs==='object'?fs.attribute:null;if(!q||q==='text'||q==='@text')return norm(item.innerText||item.textContent||'');if(String(q).startsWith('@'))return norm(item.getAttribute(String(q).slice(1))||'');let child=null;try{{child=item.querySelector(String(q))}}catch{{}}if(!child)return null;return attr?norm(child.getAttribute(attr)||''):norm(child.innerText||child.textContent||'')}};
      const out={{}};
      for(const spec of specs){{
        const root=scopeRoot(spec.scope_selector); const selectors=[spec.selector,...(spec.selector_candidates||[])].filter(Boolean).filter((v,i,a)=>a.indexOf(v)===i);
        let value=null, selector=null, selector_candidates=[], selector_unique=false, match_count=0, item_selector=spec.item_selector||null, item_fields=spec.item_fields||{{}}, exact=true;
        if(spec.value_type==='array'||spec.item_selector||Object.keys(item_fields).length){{
          for(const candidate of [spec.item_selector,...selectors].filter(Boolean).filter((v,i,a)=>a.indexOf(v)===i)){{
            if(spec.selector_type==='jsonld' || /\\s+path\\s+/i.test(String(candidate))){{
              const m=String(candidate).match(/^(.+?)\\s+path\\s+(.+)$/i);const raw=jsonldGet(m?m[2]:candidate);if(raw!==null&&raw!==undefined){{value=Array.isArray(raw)?raw:[raw];selector=m?m[2]:String(candidate);selector_candidates=[selector];selector_unique=true;match_count=value.length;break}}
            }}
            if(spec.selector_type==='xpath')continue;
            let nodes=[];try{{nodes=[...root.querySelectorAll(String(candidate))].filter(visible)}}catch{{nodes=[]}};
            if(!nodes.length)continue;
            const rows=nodes.slice(0,500).map(item=>{{const row={{}};for(const [k,fs] of Object.entries(item_fields))row[k]=itemValue(item,fs);if(!Object.keys(row).length)row.text=norm(item.innerText||item.textContent||'');return row}});
            if(rows.some(r=>Object.values(r).some(v=>v!==null&&v!==''))){{value=rows;const cs=uniqueSelector(nodes[0],root);selector=String(candidate);selector_candidates=[String(candidate),...cs].filter((v,i,a)=>v&&a.indexOf(v)===i).slice(0,8);selector_unique=nodes.length===1||!!cs[0];match_count=nodes.length;item_selector=String(candidate);break}}
          }}
        }} else if(spec.selector_type==='jsonld'){{
          const isDomSyntax = c => {{ const x=String(c||'').trim(); return !x || x.startsWith('#') || x.startsWith('.') || x.startsWith('[') || x.startsWith('/') || x.startsWith(':') || x.includes('>') || /^([A-Za-z][A-Za-z0-9_-]*)[.#:\\[]/.test(x) || ['html','head','body','main','article','section','div','span','p','a','img','h1','h2','h3','h4','ul','ol','li','time','nav','figure'].includes(x.toLowerCase()); }};
          const jCandidates = selectors.filter(c => !isDomSyntax(c));
          for(const candidate of jCandidates){{const raw=jsonldGet(candidate);if(raw!==null&&raw!==undefined&&raw!==''){{value=raw;selector=String(candidate);selector_candidates=selectors.slice(0,6);selector_unique=true;match_count=1;break}}}}
          if(value===null){{
            for(const candidate of selectors.filter(c=>isDomSyntax(c))){{let nodes=[];try{{nodes=[...root.querySelectorAll(String(candidate))].filter(visible)}}catch{{nodes=[]}}if(!nodes.length)continue;const node=nodes[0];value=readNode(node,spec);const cs=uniqueSelector(node,root);selector=String(candidate);selector_candidates=[String(candidate),...cs].filter((v,i,a)=>v&&a.indexOf(v)===i).slice(0,8);selector_unique=nodes.length===1;match_count=nodes.length;break}}
          }}
        }} else if(spec.selector_type==='meta' || spec.selector_type==='attribute'){{
          for(const candidate of selectors){{let node=null;try{{node=root.querySelector(String(candidate))}}catch{{}}if(!node && spec.selector_type==='meta'){{node=[...root.querySelectorAll('meta[content]')].find(e=>[e.getAttribute('property'),e.getAttribute('name'),e.getAttribute('itemprop')].filter(Boolean).join(' ').toLowerCase()===String(candidate).toLowerCase())}}if(!node)continue;value=readNode(node,spec);if(value){{const cs=uniqueSelector(node,root);selector=cs[0]||String(candidate);selector_candidates=cs.length?cs:[String(candidate)];selector_unique=true;match_count=1;break}}}}
        }} else if(spec.selector_type==='text'){{
          value=textValue(root,spec.selector);if(value){{selector=spec.selector;selector_candidates=selectors.slice(0,6);selector_unique=true;match_count=1}}
        }} else if(spec.selector_type==='xpath'){{
          for(const candidate of selectors){{try{{const r=document.evaluate(candidate,root,null,XPathResult.ORDERED_NODE_SNAPSHOT_TYPE,null);if(r.snapshotLength){{const node=r.snapshotItem(0);if(visible(node)){{value=readNode(node,spec);selector=candidate;selector_candidates=selectors.slice(0,6);selector_unique=r.snapshotLength===1;match_count=r.snapshotLength;break}}}}}}catch{{}}}}
        }} else {{
          for(const candidate of selectors){{let nodes=[];try{{nodes=[...root.querySelectorAll(String(candidate))].filter(visible)}}catch{{nodes=[]}}if(!nodes.length)continue;const node=nodes.length===1?nodes[0]:nodes.find(n=>norm(spec.value||'') && norm(n.innerText||n.textContent||'').includes(norm(spec.value).slice(0,80)))||nodes[0];value=readNode(node,spec);const cs=uniqueSelector(node,root);selector=String(candidate);selector_candidates=[String(candidate),...cs].filter((v,i,a)=>v&&a.indexOf(v)===i).slice(0,8);selector_unique=nodes.length===1;match_count=nodes.length;break}}
        }}
        const isCollection = spec.value_type==='array'||spec.item_selector||Object.keys(item_fields).length;
        out[spec.path]={{value,selector,selector_candidates,selector_unique,match_count,item_selector,item_fields,exact_verified:value!==null&&value!==''&&(isCollection||selector_unique)}};
      }}
      return out;
    }}"""

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
        low = str(name or "").lower()
        blocked_tokens = (
            "navigate", "reload", "go_back", "back", "forward", "close",
            "new_page", "new_tab", "tabs", "connect", "save_storage_state"
        )
        return bool(low) and not any(token in low for token in blocked_tokens)

    @staticmethod
    def _normalize_jsonld_rule(value: Any) -> tuple[str | None, bool]:
        if value is None:
            return None, False
        raw = str(value).strip()
        if not raw:
            return None, False
        m = re.match(r"^jsonld\s*:\s*(.+)$", raw, re.I)
        if m:
            return m.group(1).strip(), True
        m = re.search(r"content\s*\.\s*([A-Za-z0-9_@-]+(?:\.[A-Za-z0-9_@-]+)*)", raw, re.I)
        if m and any(token in raw.lower() for token in ("ld+json", "json.parse", "jsonld")):
            return m.group(1).strip(), True
        m = re.search(r"path\s*(?:[:=]|\b)\s*([A-Za-z0-9_@-]+(?:\.[A-Za-z0-9_@-]+)*)", raw, re.I)
        if m and any(token in raw.lower() for token in ("ld+json", "jsonld")):
            return m.group(1).strip(), True
        return raw, False

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
            selector_type = item.get("selector_type") if item.get("selector_type") in {"css", "xpath", "meta", "jsonld", "attribute", "text", "none"} else "none"
            normalized_selector, selector_was_jsonld = ExtractorAgent._normalize_jsonld_rule(selector)
            if normalized_selector:
                selector = normalized_selector
            if selector_was_jsonld:
                selector_type = "jsonld"
            if selector and GENERIC_SELECTOR_RE.fullmatch(selector.strip()) and selector_type != "jsonld":
                selector = None
                found = False if value is None else found

            selector_candidates = item.get("selector_candidates")
            if not isinstance(selector_candidates, list):
                selector_candidates = []
            normalized_candidates = []
            for raw_candidate in selector_candidates:
                candidate, _ = ExtractorAgent._normalize_jsonld_rule(raw_candidate)
                if candidate:
                    normalized_candidates.append(candidate)
            selector_candidates = list(dict.fromkeys(normalized_candidates))[:6]

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
                selector_type=selector_type,
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
                allow_semantic_fallback=False,
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
        """Merge live verification without destroying the agent's proven extraction contract.

        The LLM's primary selector is authoritative when the browser confirms that exact rule.
        Canonical selectors discovered by the verifier are added as candidates instead of replacing
        the agent's rule. This prevents a good semantic selector from being silently rewritten into
        a fragile DOM path.
        """
        proposed: dict[str, Any] = {}
        if not isinstance(verified, dict):
            missing = [f.path for f in fields if f.required and not f.found]
            for f in fields:
                set_path(proposed, f.path, f.value)
            return fields, missing, proposed

        for field in fields:
            raw = verified.get(field.path)
            if not isinstance(raw, dict):
                set_path(proposed, field.path, field.value)
                continue

            value = raw.get("value")
            exact_ok = bool(raw.get("exact_verified", bool(value not in (None, "", []) and raw.get("selector")))) and value not in (None, "", [])
            if exact_ok:
                field.value = value
                field.found = True

                # The verifier's selector is the exact candidate/rule that actually executed.
                # Canonical DOM selectors are treated as additional candidates rather than replacing it.
                verified_selector = str(raw.get("selector") or "").strip()
                original_selector = str(field.selector or "").strip()
                if verified_selector:
                    field.selector = verified_selector
                candidates: list[str] = []
                raw_candidates = raw.get("selector_candidates") if isinstance(raw.get("selector_candidates"), list) else []
                for candidate in [verified_selector, *raw_candidates]:
                    candidate = str(candidate or "").strip()
                    if candidate and candidate not in candidates:
                        candidates.append(candidate)
                if not candidates and original_selector:
                    candidates.append(original_selector)
                field.selector_candidates = candidates[:8]

                # Preserve original item selector/item fields when they are valid; enrich only missing parts.
                if raw.get("item_selector") and not field.item_selector:
                    field.item_selector = str(raw.get("item_selector"))
                if isinstance(raw.get("item_fields"), dict) and not field.item_fields:
                    field.item_fields = raw.get("item_fields") or {}
                if raw.get("scope_selector") and not field.scope_selector:
                    field.scope_selector = str(raw.get("scope_selector"))

                count = raw.get("match_count")
                if isinstance(count, (int, float)):
                    field.evidence = (field.evidence or "") + f" | verified_matches={int(count)}"
            else:
                # Live page could not reproduce the proposed rule. Do not let model-only values survive.
                field.value = None
                field.found = False

            set_path(proposed, field.path, field.value)

        missing = [f.path for f in fields if f.required and not f.found]
        return fields, missing, proposed

    async def _emit(self, cb: Callable[[str, str, dict[str, Any] | None], Awaitable[None]] | None, stage: str, message: str, meta: dict[str, Any] | None = None, level: str | None = None) -> None:
        if cb is not None:
            payload = dict(meta or {})
            if level:
                payload["level"] = level
            await cb(stage, message, payload)

    async def _get_browser_tool_defs(self) -> list[dict[str, Any]]:
        """Expose MCP browser tools except tools that can change the browsing target/session."""
        tools = await self.browser.list_tools()
        blocked_tokens = (
            "navigate", "reload", "go_back", "back", "forward", "close",
            "new_page", "new_tab", "tabs", "connect", "save_storage_state"
        )
        selected = []
        for tool in tools:
            name = str(getattr(tool, "name", ""))
            if not name:
                continue
            low = name.lower()
            if any(token in low for token in blocked_tokens):
                continue
            if not (low.startswith("browser_") or low.startswith("playwright_")):
                continue
            selected.append(tool)
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
        max_tool_calls: int | None = None,
    ) -> dict[str, Any]:
        async def executor(name: str, args: dict[str, Any]) -> Any:
            if name not in {"browser_snapshot", "browser_evaluate", "browser_click"}:
                raise RuntimeError(f"Tool not allowed in analysis phase: {name}")
            return await self.browser.call(name, args)

        async def on_event(kind: str, data: dict[str, Any]) -> None:
            if kind == "round_start":
                await self._emit(progress_cb, stage, f"LLM round {data['round']}/{data['max_rounds']}")
            elif kind == "tool_call":
                await self._emit(progress_cb, stage, f"Tool call: {data['name']}", {"round": data.get("round"), "arguments": data.get("arguments")})
            elif kind == "tool_result":
                await self._emit(progress_cb, stage, f"Tool result received: {data['name']}", {"round": data.get("round")})
            elif kind == "response_meta":
                await self._emit(
                    progress_cb,
                    stage,
                    f"LLM response: finish={data.get('finish_reason') or 'none'}, content={data.get('content_chars', 0)} chars, reasoning={data.get('reasoning_chars', 0)} chars, tools={data.get('tool_call_count', 0)}, tokens={data.get('total_tokens') or '?'}.",
                    level="debug",
                )
            elif kind == "final_response":
                if data.get("empty_recovery"):
                    await self._emit(progress_cb, stage, "LLM returned empty content; recovered with one no-tools finalization.", level="warning")
                else:
                    await self._emit(progress_cb, stage, "LLM returned the round result.")
            elif kind == "empty_response":
                await self._emit(progress_cb, stage, "LLM returned empty content; starting one no-tools recovery (no additional browser investigation).", level="warning")

        return await self.llm.chat_with_tools(
            system=system,
            user=user,
            tools=tools,
            executor=executor,
            max_rounds=max_rounds,
            max_tool_output_chars=max_output_chars,
            max_repeated_tool_calls=1,
            max_tool_calls=max_tool_calls,
            round_delay_seconds=0.2,
            on_event=on_event,
        )

    @staticmethod
    def _normalize_compare(value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip().lower()

    @classmethod
    def _field_confidence(cls, field: FieldPlan, raw_model: dict[str, Any] | None, verified: dict[str, Any] | None) -> float:
        """Score only browser-verifiable evidence; model confidence is a tiny tie-breaker."""
        raw_model = raw_model or {}
        verified = verified or {}
        value = verified.get("value")
        if value in (None, "", []):
            return 0.0

        score = 0.0

        # 40%: the exact extraction rule produced a real value in the live DOM.
        exact_live = bool(verified.get("exact_verified")) or bool(verified.get("selector") and value not in (None, "", []))
        if exact_live:
            score += 0.40

        # 20%: the proposed primary selector exists / executed.
        if verified.get("selector") or field.selector:
            score += 0.20

        # 20%: cardinality is appropriate for the requested type.
        count = verified.get("match_count")
        if field.value_type == "array":
            if isinstance(value, list) and value:
                score += 0.20
            if field.item_selector or field.item_fields:
                score += 0.05
        elif field.value_type == "object":
            if isinstance(value, dict) and value:
                score += 0.20
        else:
            # Scalars should normally resolve to one visible/intended element, but meta/JSON-LD
            # values may have count=1 by definition. Do not penalize a valid JSON-LD scalar.
            if count in (None, 1) or (isinstance(count, int) and count == 1):
                score += 0.20

        # 10%: shape/value sanity checks.
        if field.value_type == "number":
            if isinstance(value, (int, float)) or re.search(r"-?\d+(?:\.\d+)?", str(value)):
                score += 0.10
        elif field.value_type == "boolean":
            if isinstance(value, bool) or str(value).lower() in {"true", "false"}:
                score += 0.10
        elif isinstance(value, str) and value.strip():
            score += 0.10
        elif isinstance(value, (dict, list)) and value:
            score += 0.10

        # 5%: independent agreement with the short model sample, only as a tie-breaker.
        model_value = raw_model.get("value")
        if model_value not in (None, "", []):
            a = cls._normalize_compare(model_value)
            b = cls._normalize_compare(value)
            if isinstance(model_value, list) and isinstance(value, list):
                left = {cls._normalize_compare(x) for x in model_value if x not in (None, "")}
                right = {cls._normalize_compare(x) for x in value if x not in (None, "")}
                agreement = 1.0 if left and left.issubset(right) else (0.6 if left & right else 0.0)
            else:
                agreement = 1.0 if a and b and (a == b or a in b or b in a) else 0.0
            score += 0.05 * agreement

        # 5%: model confidence is never enough to make an unverified field pass.
        score += 0.05 * max(0.0, min(float(raw_model.get("confidence", 0.0) or 0.0), 1.0))
        return max(0.0, min(score, 1.0))

    @staticmethod
    def _weak_fields(fields: list[FieldPlan], threshold: float) -> list[FieldPlan]:
        return [f for f in fields if f.required and (not f.found or f.confidence < threshold or not f.selector)]

    @staticmethod
    def jsonld_inventory_js() -> str:
        return r'''() => {
          const seen = new WeakSet();
          const walk = (value, path, out, depth=0) => {
            if (depth > 20 || value === null || value === undefined) return;
            if (Array.isArray(value)) {
              value.forEach((item, i) => walk(item, `${path}[${i}]`, out, depth+1));
              return;
            }
            if (typeof value !== 'object' || seen.has(value)) return;
            seen.add(value);
            out.push({path: path || '$', value});
            for (const [key, child] of Object.entries(value)) {
              if (key === '@graph' || (child && typeof child === 'object')) {
                walk(child, path ? `${path}.${key}` : key, out, depth+1);
              }
            }
          };
          const blocks=[]; const paths=[];
          for (const [index, script] of [...document.querySelectorAll('script[type="application/ld+json"]')].entries()) {
            const raw=script.textContent||'';
            try { const parsed=JSON.parse(raw); blocks.push({index, raw_chars:raw.length, parsed}); walk(parsed, `$[${index}]`, paths); }
            catch (e) { blocks.push({index, raw_chars:raw.length, parse_error:String(e)}); }
          }
          return JSON.stringify({count:blocks.length, blocks, paths});
        }'''

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

        # One bounded preparation step. If comments/replies are requested, safely expose them when possible.
        want_collections = any(re.search(r"comment|reply|دیدگاه|نظر|پاسخ", str(x.get("path", "")), re.I) for x in contract)
        await self._emit(progress_cb, "scroll", "Scrolling the rendered page and exposing lazy-loaded content.")
        try:
            prep = await self.browser.call("browser_evaluate", {"function": prepare_page(want_collections)})
            self.last_artifacts["scroll_result"] = self._coerce_mcp_payload(prep)
        except Exception as exc:
            await self._emit(progress_cb, "scroll", f"Page preparation warning: {exc}", level="warning")

        # Save the complete rendered source for audit/artifacts. It is NOT sent wholesale to the LLM.
        try:
            source_raw = await self.browser.call("browser_evaluate", {"function": "() => document.documentElement.outerHTML"})
            source_html = self._coerce_source(source_raw)
        except Exception as exc:
            raise RuntimeError(f"Playwright MCP source capture failed: {exc}") from exc
        if not source_html.strip():
            raise RuntimeError("Playwright returned an empty rendered HTML source")
        self.last_artifacts["source_html"] = source_html

        try:
            raw_jsonld = await self.browser.call("browser_evaluate", {"function": self.jsonld_inventory_js()})
            jsonld_inventory = self._coerce_mcp_payload(raw_jsonld)
        except Exception as exc:
            jsonld_inventory = {"count": 0, "blocks": [], "paths": [], "error": str(exc)}
            await self._emit(progress_cb, "jsonld", f"JSON-LD preflight warning: {exc}", level="warning")
        self.last_artifacts["jsonld_inventory"] = jsonld_inventory
        if isinstance(jsonld_inventory, dict):
            await self._emit(progress_cb, "jsonld", f"JSON-LD preflight complete: {int(jsonld_inventory.get('count') or 0)} script block(s) parsed; inventory is now the first extraction source.")

        tool_defs = await self._get_browser_tool_defs()
        if not tool_defs:
            raise RuntimeError("Playwright MCP did not expose browser_snapshot/browser_evaluate/browser_click tools")

        direct_user = f"""
CURRENT URL: {url}
HOST: {host}

AUTHORITATIVE JSON TEMPLATE:
{json.dumps(template, ensure_ascii=False, separators=(',', ':'))}

REQUIRED LEAF CONTRACT:
{json.dumps(contract, ensure_ascii=False, separators=(',', ':'))}

You have a real Playwright browser for the CURRENT page. Investigate it yourself with the available tools.
Do NOT navigate away. The purpose is to build the most accurate Extraction Structure possible, not to summarize the page.

COMPLETE JSON-LD PREFLIGHT (already collected deterministically):
{json.dumps(jsonld_inventory, ensure_ascii=False, separators=(',', ':'))}

Process this JSON-LD inventory FIRST and COMPLETELY. For any field satisfied by JSON-LD, return
selector_type="jsonld" with an executable property path only. Do not return prose inside selector strings.
Only fields not satisfied by JSON-LD should proceed to meta/visible DOM investigation.

STRICT INVESTIGATION PRIORITIES:
- Start with the complete JSON-LD pass above.
- Then use one browser_snapshot to understand the rendered page.
- Then use targeted browser_evaluate calls to inspect the exact DOM around difficult fields. Batch multiple related fields in one evaluate call when practical.
- You may click same-page controls that clearly expand comments/replies/load-more/show-more, then inspect the changed DOM. Never navigate to another URL.
- For every field, execute/verify the exact extraction rule before returning it.
- Spend extra inspection on weak or ambiguous fields rather than producing a plausible guess.
- For each scalar field return a primary selector and up to 6 alternatives that resolve to the SAME value.
- For collections, return the repeated item selector plus relative item_fields such as @href, @src and text.
- For body/full_text, verify the container contains the COMPLETE article and excludes ads, related content, comments, sharing and navigation.
- If JSON-LD/meta and visible DOM disagree, inspect both and report only the source that is actually consistent with the requested field.

ANTI-HALLUCINATION:
- found=true only after the browser confirms the field.
- Never trust a plausible selector that has not been executed.
- Never invent values from common news-site conventions.
- Missing or ambiguous => found=false and value=null.
- The confidence number is only a report; the server will independently score the field from verification.

RETURN ONLY JSON using the requested field schema. Keep samples short; never return full body/comments/html.
"""
        self.last_artifacts["analysis_prompt"] = direct_user
        await self._emit(progress_cb, "analysis", f"Starting bounded browser-agent investigation (max {settings.llm_discovery_max_rounds} rounds / {settings.llm_discovery_max_tool_calls} tool calls).")
        analysis = await self._run_tool_agent(
            system=DIRECT_TOOL_SYSTEM,
            user=direct_user,
            tools=tool_defs,
            progress_cb=progress_cb,
            stage="analysis",
            max_rounds=settings.llm_discovery_max_rounds,
            max_output_chars=settings.llm_discovery_max_output_chars,
            max_tool_calls=settings.llm_discovery_max_tool_calls,
        )
        self.last_artifacts["analysis_response"] = analysis
        self.last_artifacts["llm_trace"] = list(getattr(self.llm, "last_trace", []))

        fields, missing, proposed = self._validate_analysis(analysis, contract)
        by_path = {str(x.get("path")): x for x in (analysis.get("fields") or []) if isinstance(x, dict)}

        await self._emit(progress_cb, "verification", "Running strict selector verification against the live browser DOM (no semantic guessing).")
        verified = {}
        try:
            raw_verified = await self.browser.call("browser_evaluate", {"function": verify_exact_selectors_js([f.model_dump() for f in fields])})
            verified = self._coerce_mcp_payload(raw_verified)
        except Exception as exc:
            await self._emit(progress_cb, f"verification", f"Exact verification failed: {exc}", level="error")
            verified = {}

        fields, missing, proposed = self._merge_verification(fields, verified)
        for field in fields:
            raw_item = by_path.get(field.path, {})
            v = verified.get(field.path) if isinstance(verified, dict) else None
            field.confidence = self._field_confidence(field, raw_item, v if isinstance(v, dict) else {})

        threshold = settings.llm_confidence_threshold
        weak = self._weak_fields(fields, threshold)
        repair_attempts = 0
        previous_weak_count = len(weak)

        while weak and repair_attempts < 2:
            repair_attempts += 1
            await self._emit(progress_cb, "repair", f"{len(weak)} field(s) are below the {threshold:.0%} verification threshold; running targeted retry {repair_attempts}/2.", meta={"paths": [f.path for f in weak], "attempt": repair_attempts})
            repair_contract = [f.model_dump() for f in weak]
            repair_user = f"""
CURRENT URL: {url}
AUTHORITATIVE TEMPLATE:
{json.dumps(template, ensure_ascii=False, separators=(',', ':'))}

WEAK FIELDS ONLY:
{json.dumps(repair_contract, ensure_ascii=False, separators=(',', ':'))}

STRICT VERIFICATION FROM PREVIOUS PASS:
{json.dumps({k: verified.get(k) for k in [f.path for f in weak] if isinstance(verified, dict)}, ensure_ascii=False, separators=(',', ':'))}

Repair ONLY these weak fields. Use the Playwright tools to inspect the current page. Verify every replacement rule.
Do not revisit strong fields. Do not navigate. Do not invent a value. Return corrected field records only.
"""
            self.last_artifacts["repair_prompt"] = repair_user
            repair = await self._run_tool_agent(
                system=REPAIR_SYSTEM,
                user=repair_user,
                tools=tool_defs,
                progress_cb=progress_cb,
                stage="repair",
                max_rounds=settings.llm_repair_max_rounds,
                max_output_chars=settings.llm_repair_max_output_chars,
                max_tool_calls=settings.llm_repair_max_tool_calls,
            )
            self.last_artifacts["repair_response"] = repair
            self.last_artifacts.setdefault("llm_trace_repair", []).extend(list(getattr(self.llm, "last_trace", [])))
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
                    merged[path] = FieldPlan.model_validate({**old.model_dump(), **item})
                fields = [merged[f.path] for f in fields]
            await self._emit(progress_cb, "verification", "Re-verifying repaired selectors against the live DOM.")
            try:
                raw_verified = await self.browser.call("browser_evaluate", {"function": verify_exact_selectors_js([f.model_dump() for f in fields])})
                verified = self._coerce_mcp_payload(raw_verified)
            except Exception as exc:
                await self._emit(progress_cb, "verification", f"Repair verification failed: {exc}", level="error")
                verified = {}
            fields, missing, proposed = self._merge_verification(fields, verified)
            by_path = {str(x.get("path")): x for x in (repair.get("fields") or []) if isinstance(x, dict)}
            for field in fields:
                v = verified.get(field.path) if isinstance(verified, dict) else None
                field.confidence = self._field_confidence(field, by_path.get(field.path, {}), v if isinstance(v, dict) else {})
            weak = self._weak_fields(fields, threshold)
            if weak and len(weak) >= previous_weak_count and repair_attempts >= 1:
                # Do not burn another full agent loop when the previous targeted attempt did not improve coverage.
                await self._emit(progress_cb, "repair", "Targeted retry did not improve the weak-field set; stopping repair to avoid a tool loop.", level="warning")
                break
            previous_weak_count = len(weak)

        # Final hallucination gate: model output is never allowed to override live verification.
        final_failures = [f.path for f in fields if f.required and (not f.found or f.confidence < threshold or not f.selector)]
        status = "READY" if not final_failures else "INCOMPLETE"
        anti_bot_state = str(analysis.get("anti_bot_state") or "NONE")
        if anti_bot_state in {"CHALLENGE", "LOGIN", "BLOCKED"}:
            status = "NEEDS_HUMAN"

        self.last_artifacts["verification"] = verified
        self.last_artifacts["confidence_threshold"] = threshold
        self.last_artifacts["final_structure"] = {
            "status": status,
            "host": host,
            "url": url,
            "template": template,
            "fields": [f.model_dump() for f in fields],
            "missing_required": final_failures,
            "confidence_threshold": threshold,
        }

        await self._emit(progress_cb, "finalize", f"Analysis complete: {status}. {len(fields)-len(final_failures)}/{len(fields)} fields are >= {threshold:.0%} verified confidence.")
        return DiscoveryResult(
            status=status,
            host=host,
            url=url,
            page_title=str(analysis.get("page_title") or ""),
            anti_bot_state=anti_bot_state,
            method="playwright",
            method_reason="Playwright MCP direct-agent analysis with bounded tool budget, strict verification, and a 95% confidence retry gate.",
            source="playwright",
            template=template,
            proposed_json=proposed,
            fields=fields,
            missing_required=final_failures,
            evidence=[str(x)[:500] for x in (analysis.get("evidence") or []) if x][:12],
            crawler_notes=[
                f"Direct Playwright agent: max {settings.llm_discovery_max_rounds} rounds / {settings.llm_discovery_max_tool_calls} tool calls.",
                f"Deterministic strict verification threshold: {threshold:.0%}.",
                "Weak fields are retried once; unverified values are rejected to prevent hallucinated extraction.",
            ] + [str(x)[:500] for x in (analysis.get("crawler_notes") or []) if x],
            trace=[
                "Playwright MCP only; no static routing or source-to-LLM blob.",
                "The LLM received the current-page Playwright tools directly and chose targeted inspections.",
                "A strict verifier executed the proposed selectors/candidates without semantic guessing.",
                f"Fields below {threshold:.0%} were retried once; remaining weak fields are marked incomplete instead of guessed.",
            ],
        )

    async def open_page(self, url: str) -> None:
        await self.browser.call("browser_navigate", {"url": url})

    async def inspect(self, url: str, template: dict[str, Any], navigate: bool = True, progress_cb: Callable[[str, str, dict[str, Any] | None], Awaitable[None]] | None = None) -> DiscoveryResult:
        contract=flatten_template(template)
        if navigate:
            await self.open_page(url)
        return await self._inspect_with_mcp(url, template, contract, progress_cb=progress_cb)
