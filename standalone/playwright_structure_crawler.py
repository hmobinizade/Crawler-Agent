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
    for value in [spec.get("selector"), *(spec.get("selector_candidates") or [])]:
        if value and value not in values:
            values.append(str(value))
    return values


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
    patterns: list[str] = []
    for part in parts:
        if re.fullmatch(r"\d+", part):
            patterns.append(r"\d+")
        elif len(part) >= 18 and re.fullmatch(r"[A-Za-z0-9_-]+", part):
            patterns.append(r"[^/]+")
        elif len(part) >= 7 and any(ch.isdigit() for ch in part) and re.fullmatch(r"[A-Za-z0-9_-]+", part):
            patterns.append(r"[^/]+")
        else:
            patterns.append(re.escape(part))
    return re.compile(r"^/" + "/".join(patterns) + r"/?$")


def same_page_type(url: str, root: str, pattern: re.Pattern[str] | None) -> bool:
    if not same_site(url, root):
        return False
    if pattern is None:
        return True
    return bool(pattern.match(urlparse(url).path or "/"))


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
        scripts = page.locator('script[type="application/ld+json"]')
        for index in range(scripts.count()):
            try:
                payload = json.loads(scripts.nth(index).inner_text(timeout=3000))
            except Exception:
                continue
            objects = payload if isinstance(payload, list) else [payload]
            for obj in objects:
                if not isinstance(obj, dict):
                    continue
                cur: Any = obj
                for part in str(path).split("."):
                    cur = cur.get(part) if isinstance(cur, dict) else None
                if cur not in (None, ""):
                    return cur
        return None

    @staticmethod
    def jsonld_value(value: Any) -> Any:
        if value in (None, ""):
            return None
        if isinstance(value, list):
            return [ExtractionEngine.jsonld_value(item) for item in value]
        if isinstance(value, dict):
            return {key: ExtractionEngine.jsonld_value(item) for key, item in value.items()}
        return clean_text(value)

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

    def _collection(self, page: Any, spec: dict[str, Any]) -> Any:
        root = self._field_root(page, spec) or page
        selectors = candidate_values(spec)
        item_selector = spec.get("item_selector")
        if item_selector and item_selector not in selectors:
            selectors.insert(0, item_selector)

        item_fields = spec.get("item_fields") or {}
        for candidate in selectors:
            jsonld_result = self._collection_from_jsonld_candidate(page, candidate, item_fields)
            if jsonld_result not in (None, "", []):
                return jsonld_result

        nodes = None
        selector_used = None
        for selector in selectors:
            try:
                locator = root.locator(selector)
                if locator.count():
                    nodes = locator
                    selector_used = selector
                    break
            except Exception:
                continue
        if nodes is None:
            return None

        rows: list[Any] = []
        for index in range(min(nodes.count(), 1000)):
            item = nodes.nth(index)
            if item_fields:
                row = {key: self._extract_item_field(item, field_spec) for key, field_spec in item_fields.items()}
                if any(value not in (None, "", []) for value in row.values()):
                    rows.append(row)
            else:
                try:
                    rows.append(clean_text(item.inner_text(timeout=2000)))
                except Exception:
                    continue

        return {"_values": rows, "_selector_used": selector_used} if rows else None

    def _field(self, page: Any, spec: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        path = spec.get("path", spec.get("name"))
        attempts: list[dict[str, Any]] = []
        selector_type = spec.get("selector_type", "none")
        value_type = spec.get("value_type", "string")
        body_like = bool(re.search(r"body|full[_\s-]*text|article[_\s-]*text|content|متن", f"{path} {spec.get('extraction_hint','')}", re.I))

        if value_type == "array" or spec.get("item_selector") or spec.get("item_fields"):
            selectors = candidate_values(spec)
            # JSON-LD arrays/objects are a first-class collection source.
            if selector_type == "jsonld":
                for candidate in selectors:
                    raw = self.jsonld_raw(page, candidate)
                    attempts.append({"candidate": candidate, "type": "jsonld", "success": raw not in (None, "")})
                    if raw not in (None, ""):
                        value = raw if isinstance(raw, list) else [raw]
                        return self.jsonld_value(value), {"path": path, "attempts": attempts, "selected": candidate, "fallback_used": candidate != spec.get("selector")}
            item_selector = spec.get("item_selector")
            if item_selector and item_selector not in selectors:
                selectors.insert(0, item_selector)
            root = self._field_root(page, spec) or page
            for candidate in selectors:
                jsonld_value = self._collection_from_jsonld_candidate(page, candidate, spec.get("item_fields") or {})
                attempts.append({"candidate": candidate, "type": "jsonld-path", "success": jsonld_value not in (None, "", [])})
                if jsonld_value not in (None, "", []):
                    return jsonld_value, {"path": path, "attempts": attempts, "selected": candidate, "fallback_used": candidate != spec.get("selector")}
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
            return None, {"path": path, "attempts": attempts, "selected": None, "fallback_used": bool(attempts)}

        root = self._field_root(page, spec) or page
        selectors = candidate_values(spec)
        for candidate in selectors:
            try:
                if selector_type == "jsonld":
                    raw = self.jsonld_raw(root, candidate)
                    attempts.append({"candidate": candidate, "type": "jsonld", "success": raw not in (None, "")})
                    if raw not in (None, ""):
                        return self.jsonld_value(raw), {"path": path, "attempts": attempts, "selected": candidate, "fallback_used": candidate != spec.get("selector")}
                elif selector_type == "meta":
                    if candidate.lstrip().startswith("meta["):
                        loc = root.locator(candidate).first
                        count = loc.count()
                        value = clean_text(loc.get_attribute(spec.get("attribute") or "content", timeout=2000)) if count else None
                    else:
                        value = self.meta_value(root, candidate)
                        count = 1 if value not in (None, "") else 0
                    attempts.append({"candidate": candidate, "type": "meta", "matches": count, "success": bool(value)})
                    if value:
                        return value, {"path": path, "attempts": attempts, "selected": candidate, "fallback_used": candidate != spec.get("selector")}
                elif selector_type == "text":
                    loc = self._text_locator(root, candidate)
                    success = bool(loc and loc.count())
                    attempts.append({"candidate": candidate, "type": "text", "success": success})
                    if success:
                        return clean_text(loc.inner_text(timeout=2000)), {"path": path, "attempts": attempts, "selected": candidate, "fallback_used": candidate != spec.get("selector")}
                else:
                    loc = self._locator(root, selector_type, candidate).first
                    count = loc.count()
                    attempts.append({"candidate": candidate, "type": selector_type, "matches": count, "success": count > 0})
                    if not count:
                        continue
                    if spec.get("attribute"):
                        value = clean_text(loc.get_attribute(spec["attribute"], timeout=2000))
                    elif body_like and selector_type == "css":
                        value = clean_body_locator(loc)
                    else:
                        value = clean_text(loc.inner_text(timeout=2000))
                    if value not in (None, ""):
                        return value, {"path": path, "attempts": attempts, "selected": candidate, "fallback_used": candidate != spec.get("selector")}
            except Exception as exc:
                attempts.append({"candidate": candidate, "type": selector_type, "success": False, "error": str(exc)[:300]})

        return None, {"path": path, "attempts": attempts, "selected": None, "fallback_used": bool(attempts)}

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
