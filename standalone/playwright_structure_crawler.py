#!/usr/bin/env python3
"""Reusable Playwright crawler driven entirely by an Extraction Structure.

This file is intentionally standalone: copy it together with any structure JSON
and run it without the Agentic Crawler Studio application.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    value = re.sub(r"\s+", " ", str(value)).strip()
    return value or None


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


def normalize_jsonld_rule(selector: Any) -> tuple[str | None, bool]:
    if selector is None:
        return None, False
    raw = str(selector).strip()
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


def looks_like_jsonld_path(selector: Any, selector_type: str | None = None) -> bool:
    path, marked = normalize_jsonld_rule(selector)
    if marked:
        return True
    if selector_type != 'jsonld' or path is None:
        return False
    x = str(path).strip()
    if not x or x.startswith(('#','.', '[', '/', ':')) or '>' in x or '(' in x or ')' in x:
        return False
    if re.match(r'^[A-Za-z][A-Za-z0-9_-]*\s*[.#:\[]', x):
        return False
    if x.lower() in {'html','head','body','main','article','section','div','span','p','a','img','h1','h2','h3','h4','ul','ol','li','time','nav','figure'}:
        return False
    return True


def set_path(obj: dict[str, Any], path: str, value: Any) -> None:
    cur = obj
    parts = str(path).split(".")
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def missing_required(data: dict[str, Any], fields: list[dict[str, Any]]) -> list[str]:
    missing: list[str] = []
    for field in fields:
        if not field.get("required", True):
            continue
        value = get_path(data, field.get("path", field.get("name", "")))
        if value in (None, "", []):
            missing.append(field.get("path", field.get("name", "")))
    return missing


def candidate_values(spec: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for raw in [spec.get("selector"), *(spec.get("selector_candidates") or [])]:
        normalized, marked = normalize_jsonld_rule(raw)
        if marked:
            value = f"jsonld:{normalized}" if normalized else ""
        else:
            value = normalized or (str(raw).strip() if raw is not None else "")
        if value and value not in values:
            values.append(value)
    return values


def clean_body_locator(locator: Any) -> str | None:
    script = r"""root => {
      const norm=s=>String(s??'').replace(/\u00a0/g,' ').replace(/\s+/g,' ').trim();
      const bad=/(^|[-_\s])(ad|ads|advert|advertisement|banner|promo|sponsor|sponsored|related|recommend|recommended|comments?|comment-section|share|sharing|social|newsletter|subscribe|cookie|popup|modal|sidebar|breadcrumb|toolbar|navigation)([-_\s]|$)/i;
      const badText=/(advertisement|تبلیغ|آگهی|مطالب مرتبط|اخبار مرتبط|دیدگاه|نظرات کاربران|اشتراک گذاری|عضویت|خبرهای مرتبط|پیشنهاد سردبیر|مطالب پیشنهادی)/i;
      if(!root) return null;
      const clone=root.cloneNode(true);
      for(const e of [...clone.querySelectorAll('script,style,noscript,iframe,svg,form')]) e.remove();
      for(const e of [...clone.querySelectorAll('*')]){
        const meta=[e.id||'',typeof e.className==='string'?e.className:'',e.getAttribute('aria-label')||'',e.getAttribute('role')||'',e.getAttribute('data-ad')||''].join(' ');
        const t=norm(e.innerText||'');
        const leaf=!e.children.length;
        const safe=/^(P|LI|A|SPAN|BUTTON)$/.test(e.tagName)||leaf;
        if(bad.test(meta)||(safe&&t.length<350&&badText.test(t))) e.remove();
      }
      const blocks=[...clone.querySelectorAll('p,h2,h3,h4,blockquote,pre,li')]
        .map(e=>norm(e.innerText||e.textContent||''))
        .filter(t=>t.length>=20&&!(t.length<500&&badText.test(t)));
      const out=[]; for(const t of blocks) if(!out.length||out[out.length-1]!==t) out.push(t);
      return out.length?out.join('\n\n'):(norm(clone.innerText||'')||null);
    }"""
    try:
        return locator.evaluate(script) or None
    except Exception:
        return None


class ExtractionEngine:
    """Execute an Extraction Structure against a Playwright page only."""

    def __init__(self, structure: dict[str, Any]):
        self.structure = structure
        self.fields = self._fields(structure)

    @staticmethod
    def _fields(structure: dict[str, Any]) -> list[dict[str, Any]]:
        fields = structure.get("fields")
        if isinstance(fields, list):
            return fields
        extraction = structure.get("extraction")
        if isinstance(extraction, dict):
            items: list[dict[str, Any]] = []
            for name, spec in extraction.items():
                item = dict(spec) if isinstance(spec, dict) else {"selector": spec}
                item.setdefault("name", name)
                item.setdefault("path", name)
                items.append(item)
            return items
        return []

    @staticmethod
    def jsonld_raw(page: Any, path: str) -> Any:
        normalized, marked = normalize_jsonld_rule(path)
        path = str(normalized or path or '').strip().strip('.')
        if not path:
            return None

        def flatten(value: Any):
            if isinstance(value, list):
                for item in value:
                    yield from flatten(item)
            elif isinstance(value, dict):
                yield value
                for child in value.values():
                    if isinstance(child, (dict, list)):
                        yield from flatten(child)

        def get(obj: Any, key_path: str) -> Any:
            cur = obj
            for part in key_path.split('.'):
                if isinstance(cur, dict):
                    cur = cur.get(part)
                else:
                    return None
            return cur

        scripts = page.locator('script[type="application/ld+json"]')
        for index in range(scripts.count()):
            try:
                payload = json.loads(scripts.nth(index).inner_text(timeout=3000))
            except Exception:
                continue
            for obj in flatten(payload):
                raw = get(obj, path)
                if raw not in (None, ''):
                    return raw
        return None

    @staticmethod
    def jsonld_value(value: Any) -> Any:
        if value in (None, ""):
            return None
        if isinstance(value, list):
            return [ExtractionEngine.jsonld_value(item) for item in value]
        if isinstance(value, dict):
            return {key: ExtractionEngine.jsonld_value(item) for key, item in value.items()}
        if isinstance(value, str):
            return clean_text(value)
        return value

    @staticmethod
    def coerce_value(value: Any, value_type: str) -> Any:
        if value in (None, ""):
            return None
        if value_type == "string":
            return clean_text(value)
        if value_type == "number":
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return value
            raw = normalize_digits(value)
            if raw is None:
                return None
            raw = re.sub(r"[^0-9.+-]", "", raw)
            try:
                return float(raw) if "." in raw else int(raw)
            except ValueError:
                return None
        if value_type == "boolean":
            if isinstance(value, bool):
                return value
            raw = str(value).strip().lower()
            if raw in {"true", "1", "yes", "بله"}:
                return True
            if raw in {"false", "0", "no", "خیر"}:
                return False
            return None
        if value_type == "array":
            return value if isinstance(value, list) else [value]
        if value_type == "object":
            return value if isinstance(value, dict) else None
        return value

    @staticmethod
    def meta_value(page: Any, key: str) -> Any:
        key = str(key).lower()
        nodes = page.locator("meta[content]")
        for index in range(nodes.count()):
            node = nodes.nth(index)
            candidate = " ".join(
                filter(
                    None,
                    [node.get_attribute("property"), node.get_attribute("name"), node.get_attribute("itemprop")],
                )
            ).lower()
            if candidate == key:
                return clean_text(node.get_attribute("content"))
        return None

    @staticmethod
    def _field_root(page: Any, spec: dict[str, Any]) -> Any:
        scope = spec.get("scope_selector")
        if not scope:
            return page
        try:
            locator = page.locator(scope)
            return locator.first if locator.count() else None
        except Exception:
            return None

    @staticmethod
    def _locator(root: Any, selector_type: str, selector: str) -> Any:
        if selector_type == "xpath":
            return root.locator(f"xpath={selector}")
        return root.locator(selector)

    @staticmethod
    def _text_locator(root: Any, text: str) -> Any:
        try:
            locator = root.get_by_text(text, exact=True)
            return locator.first if locator.count() else None
        except Exception:
            return None

    def _extract_item_field(self, item: Any, field_spec: Any) -> Any:
        if isinstance(field_spec, dict):
            query = field_spec.get("selector") or field_spec.get("path") or field_spec.get("field")
            attribute = field_spec.get("attribute")
            selector_type = field_spec.get("selector_type", "css")
        else:
            query = field_spec
            attribute = None
            selector_type = "css"

        if query in {"@href", "href"}:
            return clean_text(item.get_attribute("href"))
        if query in {"@src", "src"}:
            return clean_text(item.get_attribute("src"))
        if query in {"@text", "text", "title"}:
            try:
                return clean_text(item.inner_text(timeout=2000))
            except Exception:
                return None
        if isinstance(query, str) and query.startswith("@"):
            return clean_text(item.get_attribute(query[1:]))
        if not query:
            return clean_text(item.inner_text(timeout=2000))

        try:
            child = self._text_locator(item, str(query)) if selector_type == "text" else self._locator(item, selector_type, str(query)).first
            if not child or not child.count():
                return None
            if attribute:
                return clean_text(child.get_attribute(attribute, timeout=2000))
            return clean_text(child.inner_text(timeout=2000))
        except Exception:
            return None

    def _collection_from_jsonld_candidate(self, page: Any, candidate: str, item_fields: dict[str, Any]) -> Any:
        if not isinstance(candidate, str) or " path " not in candidate.lower():
            return None
        path = re.split(r"\s+path\s+", candidate, maxsplit=1, flags=re.I)[-1].strip()
        raw = self.jsonld_raw(page, path)
        if raw in (None, ""):
            return None
        values = raw if isinstance(raw, list) else [raw]
        output: list[Any] = []
        for value in values:
            if not isinstance(value, dict):
                output.append(value)
                continue
            row: dict[str, Any] = {}
            for key, fs in item_fields.items():
                query = fs.get("selector") if isinstance(fs, dict) else fs
                query = fs.get("path", query) if isinstance(fs, dict) else query
                if query in {"url", "@href"}:
                    row[key] = value.get("url") or value.get("@id")
                elif query in {"text", "@text", "title"}:
                    row[key] = value.get("title") or value.get("headline") or value.get("name")
                elif isinstance(query, str) and query.startswith("@"):
                    row[key] = value.get(query[1:])
                else:
                    row[key] = get_path(value, str(query))
            output.append(row if row else value)
        return output or None

    def _special_value(self, page: Any, spec: dict[str, Any]) -> Any:
        joined = " ".join([
            str(spec.get("name", "")),
            str(spec.get("path", "")),
            str(spec.get("extraction_hint", "")),
        ]).lower()

        if re.search(r"news[_\s-]*code|article[_\s-]*id|news[_\s-]*id", joined):
            value = self.meta_value(page, "og:url")
            if value:
                match = re.search(r"/fa/news/(\d+)", str(value))
                if match:
                    return match.group(1)
            try:
                text = clean_text(page.locator("body").inner_text(timeout=3000)) or ""
            except Exception:
                text = ""
            match = re.search(r"کد خبر\s*[:：]?\s*([0-9۰-۹]+)", text)
            return normalize_digits(match.group(1)) if match else None

        if re.search(r"view[_\s-]*count|views?|بازدید", joined):
            try:
                text = clean_text(page.locator("body").inner_text(timeout=3000)) or ""
            except Exception:
                text = ""
            match = re.search(r"([\d۰-۹,]+)\s*(?:بازدید|views?)", text, re.I)
            return normalize_digits(match.group(1)) if match else None

        if re.search(r"image|photo|thumbnail|picture|تصویر", joined):
            for key in ("og:image", "twitter:image"):
                value = self.meta_value(page, key)
                if value:
                    return value
            raw = self.jsonld_raw(page, "image") or self.jsonld_raw(page, "thumbnailUrl")
            if isinstance(raw, list) and raw:
                raw = raw[0]
            if isinstance(raw, dict):
                raw = raw.get("url") or raw.get("@id")
            if raw:
                return clean_text(raw)
            for selector in (
                'img.lead_image.img-fluid.img-responsive-news',
                '[itemprop="image"]',
                'main img',
                'article img',
            ):
                try:
                    node = page.locator(selector).first
                    if node.count():
                        value = clean_text(
                            node.get_attribute("src", timeout=1500)
                            or node.get_attribute("data-src", timeout=500)
                            or node.get_attribute("data-lazy-src", timeout=500)
                        )
                        if value:
                            return value
                except Exception:
                    pass

        if re.search(r"^title$|headline|عنوان", joined):
            for selector in ("h1.Htag", 'h1[itemprop="headline"]', "h1", ".article-title", ".news-title"):
                try:
                    value = clean_text(page.locator(selector).first.inner_text(timeout=1500))
                    if value:
                        return value
                except Exception:
                    pass
            return self.jsonld_raw(page, "headline") or self.meta_value(page, "og:title")

        if re.search(r"author|byline|نویسنده", joined):
            for key in ("author", "article:author", "dc.creator"):
                value = self.meta_value(page, key)
                if value:
                    return value
            value = self.jsonld_raw(page, "author")
            if isinstance(value, dict):
                value = value.get("name") or value.get("@id")
            if value:
                return clean_text(value)
            for selector in ('[rel="author"]', '[itemprop="author"]', ".author", ".byline", '[class*="author"]'):
                try:
                    value = clean_text(page.locator(selector).first.inner_text(timeout=1500))
                    if value:
                        return value
                except Exception:
                    pass

        if re.search(r"date|published|publish|تاریخ", joined):
            for key in ("article:published_time", "datePublished", "date", "dc.date", "pubdate"):
                value = self.meta_value(page, key)
                if value:
                    return value
                raw = self.jsonld_raw(page, key)
                if raw:
                    return clean_text(raw)

        if re.search(r"body|full[_\s-]*text|article[_\s-]*text|content|متن", joined):
            selectors = [
                '[itemprop="articleBody"]',
                '[data-article-body]',
                '[class*="article-body"]',
                '[class*="article-content"]',
                '[class*="story-body"]',
                '[class*="news-body"]',
                'article',
                'main',
            ]
            best = None
            best_score = -1
            for selector in selectors:
                try:
                    nodes = page.locator(selector)
                    for index in range(min(nodes.count(), 20)):
                        node = nodes.nth(index)
                        text = clean_body_locator(node) or ""
                        if len(text) < 120:
                            continue
                        score = node.locator("p").count() * 600 + min(len(text), 50000) / 6
                        if score > best_score:
                            best, best_score = text, score
                except Exception:
                    continue
            return best

        return None

    def _field(self, page: Any, spec: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        path = spec.get("path", spec.get("name"))
        attempts: list[dict[str, Any]] = []
        selector_type = spec.get("selector_type", "none")
        value_type = spec.get("value_type", "string")
        body_like = bool(re.search(r"body|full[_\s-]*text|article[_\s-]*text|content|متن", f"{path} {spec.get('extraction_hint','')}", re.I))

        if value_type == "array" or spec.get("item_selector") or spec.get("item_fields"):
            selectors = candidate_values(spec)
            # JSON-LD is always attempted first for collections.
            primary = candidate_values({"selector": spec.get("selector")})
            jsonld_selectors = [c for c in selectors if looks_like_jsonld_path(c, selector_type)]
            for candidate in jsonld_selectors:
                raw = self.jsonld_raw(page, candidate)
                attempts.append({"candidate": candidate, "type": "jsonld", "success": raw not in (None, "")})
                if raw not in (None, ""):
                    values = raw if isinstance(raw, list) else [raw]
                    item_fields = spec.get("item_fields") or {}
                    if item_fields and all(isinstance(v, dict) for v in values):
                        mapped = []
                        for value in values:
                            row = {}
                            for key, fs in item_fields.items():
                                query = fs.get("selector") if isinstance(fs, dict) else fs
                                query = fs.get("path", query) if isinstance(fs, dict) else query
                                if query in {"url", "@href"}:
                                    row[key] = value.get("url") or value.get("@id")
                                elif query in {"text", "@text", "title"}:
                                    row[key] = value.get("title") or value.get("headline") or value.get("name")
                                elif isinstance(query, str) and query.startswith("@"):
                                    row[key] = value.get(query[1:])
                                else:
                                    row[key] = get_path(value, str(query))
                            mapped.append(row)
                        values = mapped
                    return self.jsonld_value(values), {"path": path, "attempts": attempts, "selected": candidate, "selected_type": "jsonld", "fallback_used": False}

            item_selector = spec.get("item_selector")
            if item_selector and item_selector not in selectors:
                selectors.insert(0, item_selector)
            root = self._field_root(page, spec) or page
            for candidate in selectors:
                if looks_like_jsonld_path(candidate, selector_type):
                    continue
                try:
                    locator = root.locator(candidate)
                    count = locator.count()
                    attempts.append({"candidate": candidate, "type": selector_type, "matches": count, "success": count > 0})
                    if not count:
                        continue
                    rows: list[Any] = []
                    for index in range(min(count, 1000)):
                        item = locator.nth(index)
                        if spec.get("item_fields"):
                            row = {key: self._extract_item_field(item, fs) for key, fs in (spec.get("item_fields") or {}).items()}
                            if any(value not in (None, "", []) for value in row.values()):
                                rows.append(row)
                        else:
                            rows.append(clean_text(item.inner_text(timeout=2000)))
                    if rows:
                        return rows, {"path": path, "attempts": attempts, "selected": candidate, "fallback_used": candidate != spec.get("selector")}
                except Exception as exc:
                    attempts.append({"candidate": candidate, "type": selector_type, "success": False, "error": str(exc)[:300]})
            return None, {"path": path, "attempts": attempts, "selected": None, "fallback_used": False}

        root = self._field_root(page, spec) or page
        selectors = candidate_values(spec)
        primary = candidate_values({"selector": spec.get("selector")})
        jsonld_selectors = [c for c in selectors if looks_like_jsonld_path(c, selector_type)]
        for candidate in jsonld_selectors:
            try:
                raw = self.jsonld_raw(page, candidate)
                attempts.append({"candidate": candidate, "type": "jsonld", "success": raw not in (None, "")})
                if raw not in (None, ""):
                    return self.coerce_value(self.jsonld_value(raw), value_type), {"path": path, "attempts": attempts, "selected": candidate, "selected_type": "jsonld", "fallback_used": False}
            except Exception as exc:
                attempts.append({"candidate": candidate, "type": "jsonld", "success": False, "error": str(exc)[:300]})

        for candidate in selectors:
            if looks_like_jsonld_path(candidate, selector_type):
                continue
            try:
                if selector_type == "meta":
                    if candidate.lstrip().startswith("meta["):
                        loc = root.locator(candidate).first
                        count = loc.count()
                        value = clean_text(loc.get_attribute(spec.get("attribute") or "content", timeout=2000)) if count else None
                    else:
                        value = self.meta_value(root, candidate)
                        count = 1 if value not in (None, "") else 0
                    attempts.append({"candidate": candidate, "type": "meta", "matches": count, "success": bool(value)})
                    if value:
                        return self.coerce_value(value, value_type), {"path": path, "attempts": attempts, "selected": candidate, "fallback_used": candidate != spec.get("selector")}
                elif selector_type == "text":
                    loc = self._text_locator(root, candidate)
                    success = bool(loc and loc.count())
                    attempts.append({"candidate": candidate, "type": "text", "success": success})
                    if success:
                        return clean_text(loc.inner_text(timeout=2000)), {"path": path, "attempts": attempts, "selected": candidate, "fallback_used": candidate != spec.get("selector")}
                else:
                    loc = self._locator(root, selector_type, candidate)
                    count = loc.count()
                    attempts.append({"candidate": candidate, "type": selector_type, "matches": count, "success": count > 0})
                    if not count:
                        continue

                    selected = loc.first
                    if count > 1:
                        # Never silently take the first DOM node. A short model sample may disambiguate
                        # a duplicated selector, otherwise this candidate is rejected and the next
                        # deterministic candidate is attempted.
                        sample = clean_text(spec.get("value"))
                        if sample:
                            matched = None
                            for index in range(min(count, 50)):
                                candidate_node = loc.nth(index)
                                try:
                                    if spec.get("attribute"):
                                        observed = clean_text(candidate_node.get_attribute(spec["attribute"], timeout=2000))
                                    elif body_like and selector_type == "css":
                                        observed = clean_body_locator(candidate_node)
                                    else:
                                        observed = clean_text(candidate_node.inner_text(timeout=2000))
                                except Exception:
                                    observed = None
                                if observed and (observed == sample or sample in observed or observed in sample):
                                    matched = candidate_node
                                    break
                            if matched is None:
                                attempts.append({"candidate": candidate, "type": selector_type, "matches": count, "success": False, "reason": "non_unique"})
                                continue
                            selected = matched
                        else:
                            attempts.append({"candidate": candidate, "type": selector_type, "matches": count, "success": False, "reason": "non_unique"})
                            continue

                    if spec.get("attribute"):
                        value = clean_text(selected.get_attribute(spec["attribute"], timeout=2000))
                    elif body_like and selector_type == "css":
                        value = clean_body_locator(selected)
                    else:
                        value = clean_text(selected.inner_text(timeout=2000))
                    if value not in (None, ""):
                        return self.coerce_value(value, value_type), {"path": path, "attempts": attempts, "selected": candidate, "fallback_used": False}
            except Exception as exc:
                attempts.append({"candidate": candidate, "type": selector_type, "success": False, "error": str(exc)[:300]})

        if bool(spec.get("allow_semantic_fallback", False)):
            fallback = self._special_value(page, spec)
            if fallback not in (None, "", []):
                return fallback, {"path": path, "attempts": attempts, "selected": None, "fallback_used": True, "fallback": "semantic"}
        return None, {"path": path, "attempts": attempts, "selected": None, "fallback_used": False}

    def extract(self, page: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        data: dict[str, Any] = {}
        trace: list[dict[str, Any]] = []
        for spec in self.fields:
            value, field_trace = self._field(page, spec)
            path = spec.get("path", spec.get("name"))
            set_path(data, path, value)
            trace.append({
                **field_trace,
                "selector": spec.get("selector"),
                "candidates": candidate_values(spec),
                "found": value not in (None, "", []),
                "value_type": spec.get("value_type"),
            })
        return data, trace


def scroll_to_bottom(page: Any) -> None:
    page.evaluate(
        """async()=>{
          const sleep=ms=>new Promise(r=>setTimeout(r,ms));
          let last=0,stable=0;
          for(let i=0;i<30;i++){
            window.scrollTo({top:document.documentElement.scrollHeight,behavior:'smooth'});
            await sleep(220);
            const h=Math.max(document.body?.scrollHeight||0,document.documentElement.scrollHeight||0);
            if(h===last) stable++; else stable=0;
            last=h;
            if(stable>=2) break;
          }
          window.scrollTo({top:0,behavior:'smooth'});
          await sleep(250);
        }"""
    )


def crawl_playwright(
    structure: dict[str, Any],
    start_url: str,
    output: Path,
    headless: bool,
    delay: float,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Extract exactly one URL with Playwright using one Extraction Structure."""
    from playwright.sync_api import sync_playwright

    if not start_url.startswith(("http://", "https://")):
        raise ValueError("start_url must be an absolute HTTP(S) URL")

    crawl_cfg = structure.get("crawl") if isinstance(structure.get("crawl"), dict) else {}
    navigation_timeout = int(crawl_cfg.get("navigation_timeout_ms", max(60_000, timeout_seconds * 1000)))
    page_timeout = int(crawl_cfg.get("page_timeout_ms", 8_000))
    context_options = {
        "viewport": {"width": 1365, "height": 900},
        "locale": crawl_cfg.get("locale", "en-US"),
    }
    user_agent = crawl_cfg.get("user_agent")
    if user_agent:
        context_options["user_agent"] = user_agent

    output.parent.mkdir(parents=True, exist_ok=True)
    engine = ExtractionEngine(structure)
    records: list[dict[str, Any]] = []
    started = time.time()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context(**context_options)
        page = context.new_page()
        page.set_default_timeout(page_timeout)
        page.set_default_navigation_timeout(navigation_timeout)
        try:
            if time.time() - started > timeout_seconds:
                raise TimeoutError(f"Crawler timed out after {timeout_seconds}s")
            try:
                page.goto(start_url, wait_until="domcontentloaded", timeout=navigation_timeout)
                page.wait_for_timeout(1200)
                scroll_to_bottom(page)
                data, trace = engine.extract(page)
                missing = missing_required(data, engine.fields)
                record = {
                    "url": page.url,
                    "data": data,
                    "missing_required": missing,
                    "extraction_ok": not missing,
                    "method": "playwright",
                    "extraction_trace": trace,
                }
            except Exception as exc:
                record = {
                    "url": start_url,
                    "data": {},
                    "missing_required": ["__page__"],
                    "extraction_ok": False,
                    "method": "playwright",
                    "error": str(exc),
                }
            records.append(record)
            with output.open("w", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        finally:
            context.close()
            browser.close()

    failed = sum(1 for record in records if not record.get("extraction_ok"))
    return {
        "records": records,
        "record_count": 1,
        "success_count": 0 if failed else 1,
        "failed_count": failed,
        "output_path": str(output),
        "method": "playwright",
    }

def run_structure(
    structure: dict[str, Any],
    *,
    start_url: str,
    output: str | Path,
    timeout_seconds: int = 240,
    headless: bool = True,
) -> dict[str, Any]:
    """Run a generic Playwright crawler from a URL + Extraction Structure."""
    execution = structure.get("execution") if isinstance(structure.get("execution"), dict) else {}
    mode = execution.get("mode", "generic")
    if mode != "generic":
        raise ValueError(
            "This standalone runtime only executes generic structures. "
            "Use the Studio custom crawler flow for execution.mode=custom."
        )
    crawl_cfg = structure.get("crawl") if isinstance(structure.get("crawl"), dict) else {}
    delay = float(crawl_cfg.get("delay_seconds", 1.5))
    return crawl_playwright(
        structure,
        start_url=start_url,
        output=Path(output).resolve(),
        headless=headless,
        delay=delay,
        timeout_seconds=timeout_seconds,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone Playwright Structure Crawler")
    parser.add_argument("--url", required=True, help="Start URL")
    parser.add_argument("--structure", required=True, help="Path to Extraction Structure JSON")
    parser.add_argument("--out", default="crawl_results.jsonl", help="Output JSONL path")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--headed", action="store_true", help="Show the browser window")
    args = parser.parse_args()

    structure = json.loads(Path(args.structure).read_text(encoding="utf-8"))
    result = run_structure(
        structure,
        start_url=args.url,
        output=args.out,
        timeout_seconds=args.timeout,
        headless=not args.headed,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
