from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlparse


# -----------------------------
# Generic extraction primitives
# -----------------------------

def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    s = re.sub(r"\s+", " ", str(value)).strip()
    return s or None


def normalize_digits(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")).replace(",", "")


def get_path(obj: Any, path: str) -> Any:
    cur = obj
    for part in str(path).split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def set_path(obj: dict[str, Any], path: str, value: Any) -> None:
    cur = obj
    parts = str(path).split(".")
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def missing_required(data: dict[str, Any], fields: list[dict[str, Any]]) -> list[str]:
    out = []
    for field in fields:
        if not field.get("required", True):
            continue
        value = get_path(data, field.get("path", field.get("name", "")))
        if value in (None, "", []):
            out.append(field.get("path", field.get("name", "")))
    return out


def candidate_values(spec: dict[str, Any]) -> list[str]:
    vals: list[str] = []
    for value in [spec.get("selector"), *(spec.get("selector_candidates") or [])]:
        if value and value not in vals:
            vals.append(str(value))
    return vals


def same_site(url: str, root: str) -> bool:
    return urlparse(url).netloc.lower() == urlparse(root).netloc.lower()


def build_page_pattern(structure: dict[str, Any], root: str) -> re.Pattern[str] | None:
    crawl = structure.get("crawl") if isinstance(structure.get("crawl"), dict) else {}
    site = structure.get("site") if isinstance(structure.get("site"), dict) else {}
    raw = crawl.get("page_pattern") or site.get("url_pattern") or structure.get("url_pattern")
    if raw:
        try:
            return re.compile(str(raw))
        except re.error:
            return None
    path = urlparse(root).path or "/"
    parts = [p for p in path.split("/") if p]
    if not parts:
        return re.compile(r"^/$")
    out: list[str] = []
    for part in parts:
        if re.fullmatch(r"\d+", part):
            out.append(r"\d+")
        elif len(part) >= 18 and re.fullmatch(r"[A-Za-z0-9_-]+", part):
            out.append(r"[^/]+")
        elif len(part) >= 7 and any(ch.isdigit() for ch in part) and re.fullmatch(r"[A-Za-z0-9_-]+", part):
            out.append(r"[^/]+")
        else:
            out.append(re.escape(part))
    return re.compile(r"^/" + "/".join(out) + r"/?$")


def same_page_type(url: str, root: str, pattern: re.Pattern[str] | None) -> bool:
    if not same_site(url, root):
        return False
    if pattern is None:
        return True
    return bool(pattern.match(urlparse(url).path or "/"))


class ExtractionEngine:
    def __init__(self, structure: dict[str, Any], mode: str):
        self.structure = structure
        self.mode = mode
        self.fields = self._fields(structure)

    @staticmethod
    def _fields(structure: dict[str, Any]) -> list[dict[str, Any]]:
        fields = structure.get("fields")
        if isinstance(fields, list):
            return fields
        extraction = structure.get("extraction")
        if isinstance(extraction, dict):
            out = []
            for name, spec in extraction.items():
                item = dict(spec) if isinstance(spec, dict) else {"selector": spec}
                item.setdefault("name", name)
                item.setdefault("path", name)
                out.append(item)
            return out
        return []

    # ---------- JSON-LD ----------
    @staticmethod
    def jsonld_objects_bs4(soup: Any) -> list[dict[str, Any]]:
        objects: list[dict[str, Any]] = []
        for script in soup.select('script[type="application/ld+json"]'):
            raw = script.string or script.get_text()
            if not raw:
                continue
            try:
                payload = json.loads(raw.strip())
            except Exception:
                continue
            values = payload if isinstance(payload, list) else [payload]
            objects.extend(x for x in values if isinstance(x, dict))
        return objects

    @staticmethod
    def jsonld_raw_bs4(soup: Any, path: str) -> Any:
        # Support dotted paths plus @id style keys.
        for obj in ExtractionEngine.jsonld_objects_bs4(soup):
            cur: Any = obj
            for part in str(path).split("."):
                if isinstance(cur, dict):
                    cur = cur.get(part)
                else:
                    cur = None
                    break
            if cur not in (None, ""):
                return cur
        return None

    @staticmethod
    def jsonld_value(value: Any) -> Any:
        if value in (None, ""):
            return None
        if isinstance(value, list):
            return [ExtractionEngine.jsonld_value(x) for x in value]
        if isinstance(value, dict):
            # Keep dictionaries when they represent real objects.
            return {
                k: ExtractionEngine.jsonld_value(v) for k, v in value.items()
            }
        return clean_text(value)

    @staticmethod
    def meta_value_bs4(soup: Any, key: str) -> Any:
        target = str(key).lower()
        for el in soup.select("meta[content]"):
            candidate = " ".join(
                filter(
                    None,
                    [el.get("property"), el.get("name"), el.get("itemprop")],
                )
            ).lower()
            if candidate == target:
                return clean_text(el.get("content"))
        return None

    # ---------- body ----------
    @staticmethod
    def clean_body_bs4(root: Any) -> str | None:
        from bs4 import BeautifulSoup

        clone = BeautifulSoup(str(root), "html.parser")
        bad = re.compile(
            r"(^|[-_\s])(ad|ads|advert|advertisement|banner|promo|sponsor|sponsored|"
            r"related|recommend|recommended|comments?|comment-section|share|sharing|"
            r"social|newsletter|subscribe|cookie|popup|modal|sidebar|breadcrumb|toolbar|navigation)([-_\s]|$)",
            re.I,
        )
        bad_text = re.compile(
            r"(advertisement|تبلیغ|آگهی|مطالب مرتبط|اخبار مرتبط|دیدگاه|نظرات کاربران|"
            r"اشتراک گذاری|عضویت|خبرهای مرتبط|پیشنهاد سردبیر|مطالب پیشنهادی)",
            re.I,
        )
        for e in clone.select("script,style,noscript,iframe,svg,form"):
            e.decompose()
        for e in list(clone.select("*")):
            attrs = e.attrs or {}
            cls = attrs.get("class", [])
            if isinstance(cls, list):
                cls = " ".join(cls)
            meta = " ".join(
                [
                    str(attrs.get("id", "")),
                    str(cls),
                    str(attrs.get("aria-label", "")),
                    str(attrs.get("role", "")),
                    str(attrs.get("data-ad", "")),
                ]
            )
            text = clean_text(e.get_text(" ", strip=True)) or ""
            leaf = not bool(e.find(recursive=False))
            safe = e.name in {"p", "li", "a", "span", "button"} or leaf
            if bad.search(meta) or (safe and len(text) < 350 and bad_text.search(text)):
                e.decompose()
        blocks: list[str] = []
        for e in clone.select("p,h2,h3,h4,blockquote,pre,li"):
            text = clean_text(e.get_text(" ", strip=True))
            if text and len(text) >= 20 and not (len(text) < 500 and bad_text.search(text)):
                if not blocks or blocks[-1] != text:
                    blocks.append(text)
        return "\n\n".join(blocks) if blocks else clean_text(clone.get_text(" ", strip=True))

    @staticmethod
    def _extract_text_bs4(root: Any, attr: str | None) -> Any:
        if attr:
            return clean_text(root.get(attr))
        return clean_text(root.get_text(" ", strip=True))

    def _scope_bs4(self, soup: Any, spec: dict[str, Any]) -> Any:
        scope = spec.get("scope_selector")
        if not scope:
            return soup
        try:
            return soup.select_one(scope)
        except Exception:
            return None

    @staticmethod
    def _find_by_text_bs4(root: Any, text: str) -> Any:
        if not text:
            return None
        wanted = clean_text(text)
        # Prefer exact visible text, then contains.
        for el in root.find_all(True):
            value = clean_text(el.get_text(" ", strip=True))
            if value == wanted:
                return el
        for el in root.find_all(True):
            value = clean_text(el.get_text(" ", strip=True))
            if value and wanted in value:
                return el
        return None

    @staticmethod
    def _xpath_bs4(root: Any, selector: str) -> Any:
        try:
            from lxml import etree, html as lxml_html
            tree = lxml_html.fromstring(str(root))
            nodes = tree.xpath(selector)
            return nodes[0] if nodes else None
        except Exception:
            return None

    @staticmethod
    def _node_value_lxml(node: Any, attr: str | None) -> Any:
        if node is None:
            return None
        if attr:
            if attr == "text":
                return clean_text(" ".join(node.itertext()))
            return clean_text(node.get(attr))
        return clean_text(" ".join(node.itertext()))

    def _collection_bs4(self, soup: Any, spec: dict[str, Any]) -> Any:
        from bs4 import BeautifulSoup
        scope = self._scope_bs4(soup, spec)
        if scope is None:
            return None
        selectors = [x for x in candidate_values(spec)]
        item_selector = spec.get("item_selector")
        if item_selector and item_selector not in selectors:
            selectors.insert(0, item_selector)
        nodes = []
        selector_used = None
        # Candidate form: "... path <jsonld.path>" lets analysis provide a JSON-LD fallback.
        for selector in selectors:
            if isinstance(selector, str) and " path " in selector.lower():
                json_path = selector.split(" path ", 1)[1].strip()
                raw = self.jsonld_raw_bs4(soup, json_path)
                if raw not in (None, ""):
                    values = raw if isinstance(raw, list) else [raw]
                    item_fields = spec.get("item_fields") or {}
                    out = []
                    for value in values:
                        if not isinstance(value, dict):
                            out.append(value)
                            continue
                        row = {}
                        for key, fs in item_fields.items():
                            q = fs.get("selector") if isinstance(fs, dict) else fs
                            q = fs.get("path", q) if isinstance(fs, dict) else q
                            attr = fs.get("attribute") if isinstance(fs, dict) else None
                            if q == "@href" or q == "url": row[key] = value.get("url") or value.get("@id")
                            elif q in {"text", "@text", "title"}: row[key] = value.get("title") or value.get("headline") or value.get("name")
                            elif isinstance(q, str) and q.startswith("@"): row[key] = value.get(q[1:])
                            else: row[key] = get_path(value, str(q))
                        out.append(row if row else value)
                    return out or None
        for selector in selectors:
            try:
                nodes = scope.select(selector)
                if nodes:
                    selector_used = selector
                    break
            except Exception:
                continue
        if not nodes:
            return None
        item_fields = spec.get("item_fields") or {}
        out: list[dict[str, Any]] = []
        for item in nodes:
            row: dict[str, Any] = {}
            for key, field_spec in item_fields.items():
                if isinstance(field_spec, dict):
                    fs = field_spec
                    q = fs.get("selector") or fs.get("path") or fs.get("field")
                    attr = fs.get("attribute")
                else:
                    q = field_spec
                    attr = None
                if q == "@href":
                    row[key] = clean_text(item.get("href"))
                    continue
                if q == "@src":
                    row[key] = clean_text(item.get("src"))
                    continue
                if q in {"text", "@text"}:
                    row[key] = clean_text(item.get_text(" ", strip=True))
                    continue
                if isinstance(q, str) and q.startswith("@"):
                    row[key] = clean_text(item.get(q[1:]))
                    continue
                if isinstance(q, str) and q.startswith("jsonld:"):
                    row[key] = self.jsonld_value(self.jsonld_raw_bs4(soup, q[7:]))
                    continue
                child = None
                if isinstance(q, str):
                    try:
                        child = item.select_one(q)
                    except Exception:
                        child = None
                if child is not None:
                    row[key] = self._extract_text_bs4(child, attr)
                else:
                    row[key] = None
            if not row:
                row["text"] = clean_text(item.get_text(" ", strip=True))
            if any(v not in (None, "", []) for v in row.values()):
                out.append(row)
        return out or None

    def _field_bs4(self, soup: Any, spec: dict[str, Any]) -> Any:
        value_type = spec.get("value_type", "string")
        if value_type == "array" or spec.get("item_selector") or spec.get("item_fields"):
            return self._collection_bs4(soup, spec)
        scope = self._scope_bs4(soup, spec)
        if scope is None:
            return None
        selector_type = spec.get("selector_type", "none")
        hint = str(spec.get("extraction_hint", "")).lower()
        path = str(spec.get("path", "")).lower()
        body_like = bool(re.search(r"body|full[_\s-]*text|article[_\s-]*text|content|متن", path + " " + hint, re.I))
        for selector in candidate_values(spec):
            try:
                if selector_type == "jsonld":
                    raw = self.jsonld_raw_bs4(scope if scope is not soup else soup, selector)
                    if raw not in (None, ""):
                        return self.jsonld_value(raw)
                elif selector_type == "meta":
                    root = scope if scope is not soup else soup
                    value = None
                    if isinstance(selector, str) and selector.lstrip().startswith("meta["):
                        node = root.select_one(selector)
                        value = clean_text(node.get(spec.get("attribute") or "content")) if node is not None else None
                    else:
                        value = self.meta_value_bs4(root, selector)
                    if value not in (None, ""):
                        return value
                elif selector_type == "text":
                    node = self._find_by_text_bs4(scope, selector)
                    if node is not None:
                        return self._extract_text_bs4(node, spec.get("attribute"))
                elif selector_type == "xpath":
                    node = self._xpath_bs4(scope, selector)
                    if node is not None:
                        return self._node_value_lxml(node, spec.get("attribute"))
                elif selector_type in {"css", "attribute"}:
                    node = scope.select_one(selector)
                    if node is not None:
                        if body_like and selector_type == "css":
                            value = self.clean_body_bs4(node)
                        else:
                            value = self._extract_text_bs4(node, spec.get("attribute"))
                        if value not in (None, ""):
                            return value
            except Exception:
                continue
        return None

    def extract_static(self, html: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        data: dict[str, Any] = {}
        trace: list[dict[str, Any]] = []
        for spec in self.fields:
            value = self._field_bs4(soup, spec)
            path = spec.get("path", spec.get("name"))
            set_path(data, path, value)
            trace.append({"path": path, "selector": spec.get("selector"), "candidates": candidate_values(spec), "found": value not in (None, "", []), "value_type": spec.get("value_type"), "mode": "requests_bs4"})
        return data, trace

    # ---------- Playwright ----------
    @staticmethod
    def _locator(page: Any, selector_type: str, selector: str, scope: Any = None) -> Any:
        root = scope or page
        if selector_type == "xpath":
            return root.locator(f"xpath={selector}")
        return root.locator(selector)

    @staticmethod
    def _playwright_jsonld(page: Any, path: str) -> Any:
        scripts = page.locator('script[type="application/ld+json"]')
        for i in range(scripts.count()):
            try:
                payload = json.loads(scripts.nth(i).inner_text(timeout=3000))
            except Exception:
                continue
            for obj in (payload if isinstance(payload, list) else [payload]):
                cur = obj
                for part in str(path).split("."):
                    cur = cur.get(part) if isinstance(cur, dict) else None
                if cur not in (None, ""):
                    return cur
        return None

    @staticmethod
    def _playwright_meta(page: Any, key: str) -> Any:
        key = str(key).lower()
        loc = page.locator("meta[content]")
        for i in range(loc.count()):
            node = loc.nth(i)
            candidate = " ".join(filter(None, [node.get_attribute("property"), node.get_attribute("name"), node.get_attribute("itemprop")])).lower()
            if candidate == key:
                return clean_text(node.get_attribute("content"))
        return None

    def _scope_pw(self, page: Any, spec: dict[str, Any]) -> Any:
        scope = spec.get("scope_selector")
        if not scope:
            return None
        try:
            return page.locator(scope).first
        except Exception:
            return None

    def _collection_pw(self, page: Any, spec: dict[str, Any]) -> Any:
        scope = self._scope_pw(page, spec)
        root = scope or page
        selectors = candidate_values(spec)
        item_selector = spec.get("item_selector")
        if item_selector and item_selector not in selectors:
            selectors.insert(0, item_selector)
        nodes = None
        for selector in selectors:
            if isinstance(selector, str) and " path " in selector.lower():
                json_path = selector.split(" path ", 1)[1].strip()
                raw = self._playwright_jsonld(page, json_path)
                if raw not in (None, ""):
                    values = raw if isinstance(raw, list) else [raw]
                    item_fields = spec.get("item_fields") or {}
                    out=[]
                    for value in values:
                        if not isinstance(value, dict): out.append(value); continue
                        row={}
                        for key, fs in item_fields.items():
                            q = fs.get("selector") if isinstance(fs,dict) else fs
                            q = fs.get("path",q) if isinstance(fs,dict) else q
                            if q in {"@href","url"}: row[key]=value.get("url") or value.get("@id")
                            elif q in {"text","@text","title"}: row[key]=value.get("title") or value.get("headline") or value.get("name")
                            elif isinstance(q,str) and q.startswith("@"): row[key]=value.get(q[1:])
                            else: row[key]=get_path(value,str(q))
                        out.append(row if row else value)
                    return out or None
            try:
                loc = root.locator(selector)
                if loc.count():
                    nodes = loc
                    break
            except Exception:
                continue
        if nodes is None:
            return None
        fields = spec.get("item_fields") or {}
        out: list[dict[str, Any]] = []
        for i in range(min(nodes.count(), 1000)):
            item = nodes.nth(i)
            row: dict[str, Any] = {}
            for key, fs in fields.items():
                if isinstance(fs, dict):
                    q = fs.get("selector") or fs.get("path") or fs.get("field")
                    attr = fs.get("attribute")
                else:
                    q, attr = fs, None
                try:
                    if q == "@href":
                        row[key] = clean_text(item.get_attribute("href"))
                    elif q == "@src":
                        row[key] = clean_text(item.get_attribute("src"))
                    elif q in {"text", "@text"}:
                        row[key] = clean_text(item.inner_text(timeout=2000))
                    elif isinstance(q, str) and q.startswith("@"):
                        row[key] = clean_text(item.get_attribute(q[1:]))
                    else:
                        child = item.locator(q).first if q else item
                        row[key] = clean_text(child.get_attribute(attr, timeout=2000) if attr else child.inner_text(timeout=2000))
                except Exception:
                    row[key] = None
            if not row:
                try:
                    row["text"] = clean_text(item.inner_text(timeout=2000))
                except Exception:
                    row["text"] = None
            if any(v not in (None, "", []) for v in row.values()):
                out.append(row)
        return out or None

    def _field_pw(self, page: Any, spec: dict[str, Any]) -> Any:
        value_type = spec.get("value_type", "string")
        if value_type == "array" or spec.get("item_selector") or spec.get("item_fields"):
            return self._collection_pw(page, spec)
        scope = self._scope_pw(page, spec)
        root = scope or page
        selector_type = spec.get("selector_type", "none")
        hint = str(spec.get("extraction_hint", "")).lower()
        path = str(spec.get("path", "")).lower()
        body_like = bool(re.search(r"body|full[_\s-]*text|article[_\s-]*text|content|متن", path + " " + hint, re.I))
        for selector in candidate_values(spec):
            try:
                if selector_type == "jsonld":
                    raw = self._playwright_jsonld(root, selector)
                    if raw not in (None, ""):
                        return raw
                elif selector_type == "meta":
                    value = None
                    if isinstance(selector, str) and selector.lstrip().startswith("meta["):
                        loc = root.locator(selector).first
                        if loc.count():
                            value = clean_text(loc.get_attribute(spec.get("attribute") or "content"))
                    else:
                        value = self._playwright_meta(root, selector)
                    if value not in (None, ""):
                        return value
                elif selector_type == "text":
                    loc = root.get_by_text(selector, exact=True).first
                    if loc.count():
                        return clean_text(loc.inner_text(timeout=2000))
                elif selector_type in {"css", "attribute", "xpath"}:
                    loc = self._locator(root, selector_type, selector, None).first
                    if loc.count():
                        value = clean_text(loc.get_attribute(spec.get("attribute"), timeout=2000) if spec.get("attribute") else loc.inner_text(timeout=2000))
                        if body_like and selector_type == "css":
                            value = self._body_pw(loc)
                        if value not in (None, ""):
                            return value
            except Exception:
                continue
        return None

    @staticmethod
    def _body_pw(locator: Any) -> str | None:
        script = r"""root => {
          const norm=s=>String(s??'').replace(/\u00a0/g,' ').replace(/\s+/g,' ').trim();
          const bad=/(^|[-_\s])(ad|ads|advert|advertisement|banner|promo|sponsor|sponsored|related|recommend|recommended|comments?|comment-section|share|sharing|social|newsletter|subscribe|cookie|popup|modal|sidebar|breadcrumb|toolbar|navigation)([-_\s]|$)/i;
          const badText=/(advertisement|تبلیغ|آگهی|مطالب مرتبط|اخبار مرتبط|دیدگاه|نظرات کاربران|اشتراک گذاری|عضویت|خبرهای مرتبط|پیشنهاد سردبیر|مطالب پیشنهادی)/i;
          if(!root) return null; const clone=root.cloneNode(true);
          for(const e of [...clone.querySelectorAll('script,style,noscript,iframe,svg,form')]) e.remove();
          for(const e of [...clone.querySelectorAll('*')]){
            const meta=[e.id||'',typeof e.className==='string'?e.className:'',e.getAttribute('aria-label')||'',e.getAttribute('role')||'',e.getAttribute('data-ad')||''].join(' ');
            const t=norm(e.innerText||''); const leaf=!e.children.length; const safe=/^(P|LI|A|SPAN|BUTTON)$/.test(e.tagName)||leaf;
            if(bad.test(meta)||(safe&&t.length<350&&badText.test(t))) e.remove();
          }
          const blocks=[...clone.querySelectorAll('p,h2,h3,h4,blockquote,pre,li')].map(e=>norm(e.innerText||e.textContent||'')).filter(t=>t.length>=20&&!(t.length<500&&badText.test(t)));
          const out=[]; for(const t of blocks) if(!out.length||out[out.length-1]!==t) out.push(t);
          return out.length?out.join('\n\n'):(norm(clone.innerText||'')||null);
        }"""
        try:
            return locator.evaluate(script)
        except Exception:
            return None

    def extract_playwright(self, page: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        data: dict[str, Any] = {}
        trace: list[dict[str, Any]] = []
        for spec in self.fields:
            value = self._field_pw(page, spec)
            path = spec.get("path", spec.get("name"))
            set_path(data, path, value)
            trace.append({"path": path, "selector": spec.get("selector"), "candidates": candidate_values(spec), "found": value not in (None, "", []), "value_type": spec.get("value_type"), "mode": "playwright"})
        return data, trace


def scroll_to_bottom(page: Any) -> None:
    page.evaluate("""async()=>{const s=ms=>new Promise(r=>setTimeout(r,ms));let last=0,stable=0;for(let i=0;i<30;i++){window.scrollTo({top:document.documentElement.scrollHeight,behavior:'smooth'});await s(220);const h=Math.max(document.body?.scrollHeight||0,document.documentElement.scrollHeight||0);if(h===last)stable++;else stable=0;last=h;if(stable>=2)break;}window.scrollTo({top:0,behavior:'smooth'});await s(250);} """)


def crawl_requests(structure: dict[str, Any], start_url: str, output: Path, max_pages: int, delay: float) -> dict[str, Any]:
    import requests
    from bs4 import BeautifulSoup
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    session = requests.Session()
    retry = Retry(total=3, connect=3, read=3, status=3, backoff_factor=1.25, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"], raise_on_status=False)
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    session.mount("http://", adapter); session.mount("https://", adapter)
    session.headers.update({"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151.0.0.0 Safari/537.36","Accept-Language":"fa-IR,fa;q=0.9,en-US;q=0.8,en;q=0.7"})
    pattern = build_page_pattern(structure, start_url)
    crawl_cfg = structure.get("crawl") if isinstance(structure.get("crawl"), dict) else {}
    timeout = int(crawl_cfg.get("timeout_seconds", 30))
    link_selector = crawl_cfg.get("link_selector") or crawl_cfg.get("article_link_selector")
    queue=[start_url]; visited=set(); records=[]
    output.parent.mkdir(parents=True, exist_ok=True)
    engine=ExtractionEngine(structure,"requests_bs4")
    with output.open("w",encoding="utf-8") as f:
        while queue and len(visited)<max_pages:
            url=queue.pop(0)
            if url in visited or not same_site(url,start_url) or not same_page_type(url,start_url,pattern): continue
            visited.add(url)
            try:
                r=session.get(url,timeout=timeout); r.raise_for_status();
                if not r.encoding: r.encoding=r.apparent_encoding
                data, trace=engine.extract_static(r.text)
                missing=missing_required(data,engine.fields)
                record={"url":r.url,"data":data,"missing_required":missing,"extraction_ok":not missing,"method":"requests_bs4","extraction_trace":trace}
                f.write(json.dumps(record,ensure_ascii=False)+"\n"); f.flush(); records.append(record)
                soup=BeautifulSoup(r.text,"html.parser")
                selectors=[link_selector] if link_selector else ["a[href]"]
                hrefs=[]
                for sel in selectors:
                    try: hrefs.extend(a.get("href") for a in soup.select(sel))
                    except Exception: pass
                for href in hrefs:
                    if href:
                        full=urljoin(r.url,href).split('#',1)[0]
                        if full not in visited and same_page_type(full,start_url,pattern): queue.append(full)
            except Exception as exc:
                record={"url":url,"data":{},"missing_required":["__request__"],"extraction_ok":False,"method":"requests_bs4","error":str(exc)}
                f.write(json.dumps(record,ensure_ascii=False)+"\n"); f.flush(); records.append(record)
            time.sleep(delay)
    failed=sum(1 for r in records if not r.get("extraction_ok"))
    return {"records":records[-100:],"record_count":len(records),"success_count":len(records)-failed,"failed_count":failed,"output_path":str(output)}


def crawl_playwright(structure: dict[str, Any], start_url: str, output: Path, max_pages: int, headless: bool, delay: float) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright
    crawl_cfg = structure.get("crawl") if isinstance(structure.get("crawl"), dict) else {}
    pattern=build_page_pattern(structure,start_url); link_selector=crawl_cfg.get("link_selector") or crawl_cfg.get("article_link_selector") or "a[href]"
    output.parent.mkdir(parents=True,exist_ok=True); records=[]; queue=[start_url]; visited=set(); engine=ExtractionEngine(structure,"playwright")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(viewport={"width":1365,"height":900})
        page.set_default_timeout(int(crawl_cfg.get("page_timeout_ms",8000)))
        try:
            with output.open("w",encoding="utf-8") as f:
                while queue and len(visited)<max_pages:
                    url=queue.pop(0)
                    if url in visited or not same_site(url,start_url) or not same_page_type(url,start_url,pattern): continue
                    visited.add(url)
                    try:
                        page.goto(url,wait_until="domcontentloaded",timeout=int(crawl_cfg.get("navigation_timeout_ms",60000))); page.wait_for_timeout(1200); scroll_to_bottom(page)
                        data,trace=engine.extract_playwright(page); missing=missing_required(data,engine.fields)
                        record={"url":page.url,"data":data,"missing_required":missing,"extraction_ok":not missing,"method":"playwright","extraction_trace":trace}
                        f.write(json.dumps(record,ensure_ascii=False)+"\n"); f.flush(); records.append(record)
                        for href in page.locator(link_selector).evaluate_all("els=>els.map(e=>e.href||e.getAttribute('href')).filter(Boolean)"):
                            full=urljoin(page.url,href).split('#',1)[0]
                            if full not in visited and same_page_type(full,start_url,pattern): queue.append(full)
                    except Exception as exc:
                        record={"url":url,"data":{},"missing_required":["__page__"],"extraction_ok":False,"method":"playwright","error":str(exc)}
                        f.write(json.dumps(record,ensure_ascii=False)+"\n"); f.flush(); records.append(record)
                    time.sleep(delay)
        finally:
            browser.close()
    failed=sum(1 for r in records if not r.get("extraction_ok"))
    return {"records":records[-100:],"record_count":len(records),"success_count":len(records)-failed,"failed_count":failed,"output_path":str(output)}


def run_structure(structure: dict[str, Any], *, start_url: str, output: str | Path, max_pages: int = 20, timeout_seconds: int = 240, headless: bool = True) -> dict[str, Any]:
    method = structure.get("method") or structure.get("execution", {}).get("method") or "requests_bs4"
    crawl_cfg=structure.get("crawl") if isinstance(structure.get("crawl"),dict) else {}
    delay=float(crawl_cfg.get("delay_seconds",1.5))
    out=Path(output).resolve()
    if method in {"playwright","dynamic","browser"}:
        return crawl_playwright(structure,start_url,out,max_pages,headless,delay)
    return crawl_requests(structure,start_url,out,max_pages,delay)


def main() -> None:
    import argparse
    parser=argparse.ArgumentParser(description="Generic crawler runtime")
    parser.add_argument("--structure", required=True)
    parser.add_argument("--url", required=False)
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--headless", action="store_true")
    args=parser.parse_args()
    structure=json.loads(Path(args.structure).read_text(encoding="utf-8"))
    start_url=args.url or structure.get("url") or structure.get("start_url")
    if not start_url: raise SystemExit("Structure has no start_url/url")
    result=run_structure(structure,start_url=start_url,output=args.out,max_pages=args.max_pages,timeout_seconds=args.timeout,headless=args.headless)
    print(json.dumps(result,ensure_ascii=False))


if __name__ == "__main__":
    main()
