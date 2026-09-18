from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

from .llm import OpenAICompatibleLLM
from .mcp_client import PlaywrightMCP
from .models import DiscoveryResult, FieldPlan
from .prompts import DISCOVERY_SYSTEM

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
    specs_json = json.dumps(specs, ensure_ascii=False, separators=(',', ':'))
    return f"""() => {{
      const specs = {specs_json};
      const norm = s => String(s ?? '').replace(/\\u00a0/g,' ').replace(/\\s+/g,' ').trim();
      const digits = s => String(s ?? '').replace(/[۰-۹]/g, d => String('۰۱۲۳۴۵۶۷۸۹'.indexOf(d))).replace(/,/g,'');
      const visible = el => {{
        if (!el) return false;
        const s=getComputedStyle(el), r=el.getBoundingClientRect();
        return s.display!=='none' && s.visibility!=='hidden' && Number(s.opacity||1)>0 && r.width>0 && r.height>0;
      }};
      const esc = s => CSS.escape(String(s));
      const uniqueSelector = el => {{
        if (!el || el.nodeType!==1) return null;
        for (const attr of ['data-testid','data-qa','data-cy']) {{
          const v=el.getAttribute(attr);
          if (v) {{ const q=`${{el.tagName.toLowerCase()}}[${{attr}}=\\"${{String(v).replaceAll('\\"','\\\\\\"')}}\\"]`; try {{ if(document.querySelectorAll(q).length===1) return q; }} catch {{}} }}
        }}
        if (el.id) {{ const q=`#${{esc(el.id)}}`; try {{ if(document.querySelectorAll(q).length===1) return q; }} catch {{}} }}
        const parts=[]; let node=el;
        for (let d=0; node && node.nodeType===1 && d<8; d++, node=node.parentElement) {{
          let part=node.tagName.toLowerCase();
          const cls=[...node.classList].filter(c=>/^[A-Za-z_][A-Za-z0-9_-]{{1,60}}$/.test(c) && !/^(active|selected|show|open|hidden)$/i.test(c)).slice(0,3);
          for (const c of cls) part += '.'+esc(c);
          const same = node.parentElement ? [...node.parentElement.children].filter(x=>x.tagName===node.tagName) : [];
          if (same.length>1) part += `:nth-of-type(${{same.indexOf(node)+1}})`;
          parts.unshift(part); const q=parts.join(' > ');
          try {{ if(document.querySelectorAll(q).length===1) return q; }} catch {{}}
        }}
        return parts.join(' > ');
      }};
      const badRe = /(^|[-_\\s])(ad|ads|advert|advertisement|banner|promo|sponsor|sponsored|related|recommend|recommended|comments?|comment-section|share|sharing|social|newsletter|subscribe|cookie|popup|modal|sidebar|breadcrumb|toolbar|navigation)([-_\\s]|$)/i;
      const badTextRe = /(advertisement|تبلیغ|آگهی|مطالب مرتبط|اخبار مرتبط|دیدگاه|نظرات کاربران|اشتراک گذاری|عضویت|خبرهای مرتبط|پیشنهاد سردبیر|مطالب پیشنهادی)/i;
      const isBad = el => {{
        if (!el || el.nodeType!==1) return true;
        if (['SCRIPT','STYLE','NOSCRIPT','IFRAME','SVG','FORM'].includes(el.tagName)) return true;
        const meta=[el.id||'', typeof el.className==='string'?el.className:'', el.getAttribute('aria-label')||'', el.getAttribute('role')||'', el.getAttribute('data-ad')||''].join(' ');
        if (badRe.test(meta)) return true;
        const t=norm(el.innerText||'');
        return t.length<350 && badTextRe.test(t);
      }};
      const cleanClone = root => {{
        const clone=root.cloneNode(true);
        for (const el of [...clone.querySelectorAll('*')]) if (isBad(el)) el.remove();
        for (const el of [...clone.querySelectorAll('img, picture, video, figure')]) {{
          const meta=[el.getAttribute('alt')||'', el.getAttribute('class')||'', el.getAttribute('id')||''].join(' ');
          if (badRe.test(meta)) el.remove();
        }}
        return clone;
      }};
      const bodyFrom = root => {{
        if (!root) return null;
        const clone=cleanClone(root);
        const blocks=[...clone.querySelectorAll('p,h2,h3,h4,blockquote,pre,li')]
          .map(x=>norm(x.innerText||x.textContent||''))
          .filter(t=>t.length>=20);
        const out=[];
        for (const t of blocks) {{
          if (badTextRe.test(t) && t.length<500) continue;
          if(!out.length || out[out.length-1]!==t) out.push(t);
        }}
        let text=out.length ? out.join('\\n\\n') : norm(clone.innerText||'');
        text=text.replace(/(?:^|\\n)(?:تبلیغ|آگهی|مطالب مرتبط|اخبار مرتبط|دیدگاه|نظرات کاربران).*?(?=\\n|$)/g,'');
        return norm(text) || null;
      }};
      const articleCandidates = () => {{
        const sels=[
          '[itemprop=\\"articleBody\\"]',
          '[data-article-body]',
          '[class*=\\"article-body\\"]', '[class*=\\"article-content\\"]',
          '[class*=\\"story-body\\"]', '[class*=\\"story-content\\"]',
          '[class*=\\"news-body\\"]', '[class*=\\"news-content\\"]',
          '[class*=\\"post-content\\"]', '[class*=\\"post-body\\"]',
          'article', '[role=\\"article\\"]', 'main'
        ];
        const seen=new Set(), arr=[];
        for (const sel of sels) for (const el of document.querySelectorAll(sel)) {{
          if(seen.has(el) || !visible(el)) continue;
          seen.add(el);
          const text=bodyFrom(el); if(!text || text.length<150) continue;
          const ps=el.querySelectorAll('p').length;
          const heading=el.querySelector('h1,h2,h3');
          const headingText=norm(heading?.innerText||'');
          const score=ps*600 + Math.min(text.length,50000)/6 + (headingText?500:0);
          arr.push({{el,text,score}});
        }}
        return arr.sort((a,b)=>b.score-a.score);
      }};
      const meta = sel => {{ try {{ const el=document.querySelector(sel); return el ? norm(el.getAttribute('content')||'') : null; }} catch {{ return null; }} }};
      const metas = () => [...document.querySelectorAll('meta[content]')].map(m=>({{key:(m.getAttribute('property')||m.getAttribute('name')||m.getAttribute('itemprop')||'').toLowerCase(),value:norm(m.content)}})).filter(x=>x.value);
      const jsonldObjs=()=>{{const a=[]; for(const s of document.querySelectorAll('script[type=\\"application/ld+json\\"]')){{try{{const x=JSON.parse(s.textContent||'');Array.isArray(x)?a.push(...x):a.push(x)}}catch{{}}}} return a;}};
      const jsonldFind = (keys) => {{
        const wanted=keys.map(k=>k.toLowerCase());
        const walk = obj => {{
          if (!obj || typeof obj!=='object') return null;
          for (const k of Object.keys(obj)) {{
            const lk=k.toLowerCase();
            if (wanted.includes(lk)) {{ const v=obj[k]; if(typeof v==='string' || typeof v==='number') return norm(v); if(v && typeof v==='object') return norm(v.name||v.url||v['@id']||''); }}
          }}
          for (const v of Object.values(obj)) {{ const got=walk(v); if(got) return got; }}
          return null;
        }};
        for(const o of jsonldObjs()) {{ const got=walk(o); if(got) return got; }}
        return null;
      }};
      const image = el => {{ if(!el) return null; if(el.tagName==='IMG') return el.currentSrc||el.src||el.getAttribute('data-src')||el.getAttribute('data-lazy-src')||null; const img=el.querySelector?.('img'); return img?(img.currentSrc||img.src||img.getAttribute('data-src')||img.getAttribute('data-lazy-src')||null):(el.getAttribute('content')||el.getAttribute('href')||null); }};
      const primaryArticleImage = () => {{
        const article=articleCandidates()[0]?.el;
        const imgs=[...(article||document).querySelectorAll('img')].filter(i=>visible(i) && (i.naturalWidth||0)>=240 && (i.naturalHeight||0)>=120);
        const good=imgs.filter(i=>!badRe.test([i.alt||'',i.className||'',i.id||'',i.closest('figure')?.className||''].join(' ')));
        return image(good[0] || imgs[0] || null);
      }};
      const visibleText = () => norm(document.body?.innerText||'');
      const firstMatch = regex => {{ const m=visibleText().match(regex); return m ? norm(m[1] || m[0]) : null; }};
      const result={{}};
      for (const spec of specs) {{
        const path=String(spec.path||''), hint=(String(spec.extraction_hint||'')+' '+path).toLowerCase();
        let value=null, chosen=spec.selector||null;
        const low=path.toLowerCase();
        // Deterministic extraction for common news fields.
        if (/news[_\\s-]*code|article[_\\s-]*id|news[_\\s-]*id|code/.test(low)) {{
          value=firstMatch(/(?:کد\\s*خبر|خبر\\s*کد|news\\s*code|article\\s*id)\\s*[:：]?\\s*([0-9۰-۹]+)/i);
          if(value) {{ value=digits(value); chosen=null; }}
        }}
        if (/view[_\\s-]*count|views?|بازدید/.test(low) && !value) {{
          value=firstMatch(/([0-9۰-۹,]+)\\s*(?:بازدید|views?)/i);
          if(value) {{ value=digits(value); chosen=null; }}
        }}
        if (/^author$|author|نویسنده|byline/.test(low) && !value) {{
          const mm=metas().find(x=>/^(author|article:author|dc.creator)$/.test(x.key));
          value=mm?.value || jsonldFind(['author','creator']);
          if(!value) {{
            const els=[...document.querySelectorAll('[rel=\\"author\\"],[itemprop=\\"author\\"],.author,.byline,[class*=\\"author\\"]')].filter(visible);
            value=norm(els[0]?.innerText||'') || null;
            if(value) chosen=uniqueSelector(els[0]);
          }}
        }}
        if (/date|published|publish|تاریخ/.test(low) && !value) {{
          const mm=metas().find(x=>/published_time|article:published_time|date|dc.date|pubdate/.test(x.key));
          value=mm?.value || jsonldFind(['datePublished','dateCreated','dateModified']);
          if(!value) value=firstMatch(/تاریخ\\s*انتشار\\s*[:：]?\\s*([^\\n|]+)/i);
          if(value && !chosen) chosen=mm ? `meta[${{mm.key.startsWith('og:')?'property':'name'}}=\\"${{mm.key}}\\"]` : null;
        }}
        if (/image|photo|thumbnail|picture|تصویر/.test(low) && !value) {{
          const mm=metas().find(x=>/og:image|twitter:image|image/.test(x.key));
          value=mm?.value || jsonldFind(['image','thumbnailUrl']);
          if(!value) value=primaryArticleImage();
          if(value && mm) chosen=`meta[${{mm.key.startsWith('og:')?'property':'name'}}=\\"${{mm.key}}\\"]`;
        }}
        if (/^title$|headline|title|عنوان/.test(low) && !value) {{
          const h=document.querySelector('h1');
          value=norm(h?.innerText||'') || jsonldFind(['headline','name']) || norm(document.title);
          if(value && h) chosen=uniqueSelector(h);
        }}
        const collectionLike = spec.value_type === 'array' || /comments?|commentary|replies|دیدگاه|نظرات|کامنت|پاسخ/.test(low);
        if (collectionLike && !value) {{
          let itemSelector = spec.item_selector || null;
          const itemFields = spec.item_fields || {{}};
          try {{
            let nodes = itemSelector ? [...document.querySelectorAll(itemSelector)].filter(visible) : [];
            if (nodes.length < 2) {{
              const candidates=[];
              for (const ps of ['[class*="comment" i]','[id*="comment" i]','[class*="reply" i]','[id*="reply" i]','[data-comment]','[data-reply]']) {{
                for (const parent of document.querySelectorAll(ps)) {{
                  const kids=[...parent.querySelectorAll(':scope > li, :scope > article, :scope > div, :scope > section')].filter(visible).filter(x=>norm(x.innerText||'').length>=12);
                  if(kids.length>=2) candidates.push({{kids,parent}});
                }}
              }}
              candidates.sort((a,b)=>b.kids.length-a.kids.length);
              if(candidates.length){{
                nodes=candidates[0].kids;
                const parentSel=uniqueSelector(candidates[0].parent);
                itemSelector=parentSel ? `${{parentSel}} > ${{nodes[0].tagName.toLowerCase()}}` : null;
              }}
            }}
            const rows=nodes.slice(0,500).map(node => {{
              const row={{}};
              for(const [key,fs] of Object.entries(itemFields)) {{
                const q=fs && fs.selector ? fs.selector : (typeof fs==='string' ? fs : null);
                const attr=fs && fs.attribute ? fs.attribute : null;
                let el=null; try {{ el=q ? node.querySelector(q) : node; }} catch {{}}
                row[key]=attr ? norm(el?.getAttribute(attr)||'') : norm(el?.innerText||el?.textContent||'');
              }}
              if(!Object.keys(row).length) row.text=norm(node.innerText||node.textContent||'');
              return row;
            }}).filter(x=>Object.values(x).some(v=>String(v||'').trim()));
            if(rows.length){{ value = spec.value_type === 'array' ? rows : rows.map(x=>Object.values(x).filter(Boolean).join(' | ')).join('\\n\\n'); chosen=itemSelector; }}
          }} catch {{}}
        }}
        const bodyLike=/body|article body|full text|article text|متن|content/.test(hint) || /(^|\\.)body$|(^|\\.)content$/i.test(path);
        if (bodyLike && !value) {{
          const c=articleCandidates()[0];
          if(c) {{ value=c.text; chosen=uniqueSelector(c.el); }}
        }}
        if (!value && spec.selector_type==='meta' && chosen) value=meta(chosen);
        if (!value && spec.selector_type==='jsonld' && chosen) value=jsonldFind([chosen]);
        if (!value && chosen) {{ try {{
          const els=[...document.querySelectorAll(chosen)].filter(visible);
          if(els.length) {{ const el=els[0]; value=bodyLike?bodyFrom(el):(/image|photo|thumbnail|picture/.test(hint)?image(el):(spec.attribute?norm(el.getAttribute(spec.attribute)||''):norm(el.innerText||el.textContent||''))); }}
        }} catch {{}} }}
        if((value===null||value==='') && bodyLike) {{ const c=articleCandidates()[0]; if(c) {{ value=c.text; chosen=uniqueSelector(c.el); }} }}
        result[path]={{value,selector:chosen,selector_unique:!!chosen && (()=>{{try{{return document.querySelectorAll(chosen).length===1}}catch{{return false}}}})()}};
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
    def _trim_evidence(value: Any, limit: int = 18000) -> str:
        if isinstance(value, (dict, list)):
            text=json.dumps(value, ensure_ascii=False)
        else:
            text=str(value or "")
        return text[:limit]

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
                value=raw.get("value")
                selector=raw.get("selector")
                unique=raw.get("selector_unique")
                if value not in (None, "", []):
                    field.value=value
                    field.found=True
                elif field.found and field.value not in (None, "", []):
                    pass
                else:
                    field.value=None
                    field.found=False
                if selector and (not field.selector or unique is True):
                    field.selector=str(selector)
                if unique is False and field.selector:
                    field.confidence=min(field.confidence, 0.65)
            set_path(proposed, field.path, field.value)
        missing=[f.path for f in fields if f.required and not f.found]
        return fields, missing, proposed

    async def _inspect_with_mcp(self, url: str, template: dict[str, Any], contract: list[dict[str, Any]]) -> DiscoveryResult:
        host=urlparse(url).netloc.lower().split(":",1)[0]

        # MCP is used only as a browser runtime here. The LLM never receives browser
        # tools and therefore cannot get stuck in a tool-calling loop.
        scroll_js = r"""async () => {
          const sleep = ms => new Promise(r => setTimeout(r, ms));
          let lastHeight = 0;
          let stable = 0;
          for (let i = 0; i < 36; i++) {
            window.scrollBy({top: Math.max(300, Math.floor(window.innerHeight * 0.82)), behavior: 'auto'});
            await sleep(250);
            const h = Math.max(document.body?.scrollHeight || 0, document.documentElement.scrollHeight || 0);
            if (h === lastHeight) stable++; else stable = 0;
            lastHeight = h;
            if (stable >= 4) break;
          }
          window.scrollTo({top: 0, behavior: 'auto'});
          await sleep(250);
          return {height:lastHeight, url:location.href, title:document.title};
        }"""
        try:
            await self.browser.call("browser_evaluate", {"function": scroll_js})
        except Exception as exc:
            raise RuntimeError(f"Playwright MCP scroll failed: {exc}") from exc

        # Capture the complete post-scroll DOM for reproducibility/code generation artifacts.
        try:
            source_html_raw = await self.browser.call(
                "browser_evaluate",
                {"function": "() => document.documentElement.outerHTML"},
            )
            source_html = self._coerce_mcp_payload(source_html_raw)
            if not isinstance(source_html, str):
                source_html = json.dumps(source_html, ensure_ascii=False)
        except Exception:
            source_html = ""

        # Compact DOM evidence: enough for the LLM to identify stable extraction rules,
        # but not the full DOM/article text.
        try:
            compact = await self.browser.call("browser_evaluate", {"function": compact_browser_evidence()})
        except Exception as exc:
            raise RuntimeError(f"Playwright MCP evidence collection failed: {exc}") from exc
        compact=self._coerce_mcp_payload(compact)
        evidence=self._trim_evidence(compact, 18000)

        user_prompt=f"""
You are a senior web extraction planner. You are NOT operating browser tools in this step.
The browser page has already been loaded and scrolled to the bottom and back to the top by Python.
Use ONLY the supplied DOM evidence to build a robust extraction specification for the generated crawler.
Do not invent selectors or values.

URL:
{url}

AUTHORITATIVE TEMPLATE:
{json.dumps(template, ensure_ascii=False, separators=(',',':'))}

LEAF CONTRACT:
{json.dumps(contract, ensure_ascii=False, separators=(',',':'))}

COMPACT BROWSER EVIDENCE:
{evidence}

IMPORTANT GENERATION RULES:
- Every template leaf must appear.
- Prefer deterministic sources: meta, JSON-LD, exact stable CSS selectors.
- For body/full_text, select the actual article-content container, not the first paragraph.
- For collection fields (comments/replies/items), identify a repeated item selector and child selectors.
- Avoid generic selectors (div, article, main, p, .content, .title, etc.) unless scoped into a unique stable selector.
- For fields with reliable semantic fallbacks such as title, image, date, author, view_count and news_code, provide a primary selector plus candidates where appropriate.
- Keep value/evidence tiny. Do not reproduce the article body or all comments.
- The generated crawler must be able to run independently of the LLM.

Return ONLY the compact JSON object described by the system schema.
"""

        self.last_artifacts = {
            "source_html": source_html,
            "compact_evidence": evidence,
            "discovery_prompt": user_prompt,
        }

        try:
            analysis=await self.llm.chat_json(DISCOVERY_SYSTEM, user_prompt)
        except Exception as exc:
            raise RuntimeError(f"LLM discovery failed: {exc}") from exc

        fields, missing, proposed=self._validate_analysis(analysis, contract)

        # One deterministic browser verification pass. This is Python -> MCP, never
        # LLM -> MCP. The verification uses the rules proposed by the LLM and captures
        # full body/collections without sending those large values back to the model.
        verify_specs=[f.model_dump() for f in fields]
        try:
            raw_verified=await self.browser.call(
                "browser_evaluate",
                {"function": verify_and_extract_js(verify_specs)},
            )
            verified=self._coerce_mcp_payload(raw_verified)
            fields, missing, proposed=self._merge_verification(fields, verified)
        except Exception:
            # Do not make an otherwise usable analysis fail only because browser-side
            # verification could not be decoded. The generated crawler still carries
            # the selector contract from the LLM response.
            pass

        anti_bot_state=str(analysis.get("anti_bot_state") or "NONE")
        status="INCOMPLETE" if missing else "READY"
        if anti_bot_state in {"CHALLENGE","LOGIN","BLOCKED"}:
            status="NEEDS_HUMAN"

        self.last_artifacts["analysis_response"] = analysis

        return DiscoveryResult(
            status=status,
            host=host,
            url=url,
            page_title=str(analysis.get("page_title") or (compact.get("page_title") if isinstance(compact,dict) else "") or ""),
            anti_bot_state=anti_bot_state,
            method="playwright",
            method_reason="Python used Playwright MCP only for browser loading, controlled scrolling and deterministic verification; the LLM received compact DOM evidence and did not call browser tools.",
            source="playwright",
            template=template,
            proposed_json=proposed,
            fields=fields,
            missing_required=missing,
            evidence=[str(x)[:300] for x in (analysis.get("evidence") or []) if x],
            crawler_notes=[
                "Generated crawler is deterministic and does not require an LLM at runtime.",
                "Browser page was scrolled gradually to the end and returned to the top before analysis.",
                *[str(x)[:500] for x in (analysis.get("crawler_notes") or []) if x],
            ],
            trace=[
                "Playwright MCP was used as a browser runtime; no LLM tool-calling loop was used.",
                "Page was scrolled in incremental viewport-sized steps to trigger lazy-loaded content.",
                "Compact DOM evidence was sent once to the LLM.",
                "A single deterministic browser-side verification pass was attempted after LLM planning.",
            ],
        )

    async def open_page(self, url: str) -> None:
        await self.browser.call("browser_navigate", {"url": url})

    async def inspect(self, url: str, template: dict[str, Any], navigate: bool = True) -> DiscoveryResult:
        contract=flatten_template(template)
        if navigate:
            await self.open_page(url)
        return await self._inspect_with_mcp(url, template, contract)
