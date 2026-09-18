from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .models import FieldPlan

BAD_CLASS_RE = re.compile(
    r"(^|[-_\s])(ad|ads|advert|advertisement|banner|promo|sponsor|sponsored|related|recommend|recommended|comments?|comment-section|share|sharing|social|newsletter|subscribe|cookie|popup|modal|sidebar|breadcrumb|toolbar|navigation)([-_\s]|$)",
    re.I,
)
BAD_TEXT_RE = re.compile(
    r"advertisement|تبلیغ|آگهی|مطالب مرتبط|اخبار مرتبط|دیدگاه|نظرات کاربران|اشتراک گذاری|عضویت|خبرهای مرتبط|پیشنهاد سردبیر|مطالب پیشنهادی",
    re.I,
)
CHALLENGE_RE = re.compile(r"captcha|verify you are human|access denied|unusual traffic|cloudflare|robot check", re.I)


def clean(value: Any) -> str | None:
    if value is None:
        return None
    s = re.sub(r"\s+", " ", str(value)).strip()
    return s or None


def get_path(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def jsonld_objects(soup: BeautifulSoup) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(tag.string or tag.get_text())
        except Exception:
            continue
        values = payload if isinstance(payload, list) else [payload]
        for item in values:
            if isinstance(item, dict):
                out.append(item)
    return out


def jsonld_find(soup: BeautifulSoup, path: str) -> Any:
    for obj in jsonld_objects(soup):
        value = get_path(obj, path)
        if isinstance(value, (str, int, float)) and value not in ("", None):
            return value
        if isinstance(value, dict):
            for k in ("name", "url", "@id"):
                if value.get(k):
                    return value[k]
    return None


def meta_value(soup: BeautifulSoup, selector: str, attribute: str | None = "content") -> Any:
    try:
        tag = soup.select_one(selector)
    except Exception:
        return None
    if not tag:
        return None
    return tag.get(attribute or "content") or tag.get_text(" ", strip=True)


def clean_body(root: Any) -> str | None:
    if not root:
        return None
    clone = BeautifulSoup(str(root), "html.parser")
    for el in clone.select("script,style,noscript,iframe,svg,form"):
        el.decompose()
    for el in list(clone.select("*")):
        if getattr(el, "attrs", None) is None:
            continue
        meta = " ".join([
            el.get("id", ""),
            " ".join(el.get("class", [])) if isinstance(el.get("class"), list) else str(el.get("class", "")),
            el.get("aria-label", ""),
            el.get("role", ""),
        ])
        text = clean(el.get_text(" ", strip=True)) or ""
        has_blocks = bool(el.select("p,h2,h3,h4,blockquote,pre"))
        if BAD_CLASS_RE.search(meta) or (len(text) < 400 and BAD_TEXT_RE.search(text) and not has_blocks):
            el.decompose()

    blocks: list[str] = []
    for el in clone.select("p,h2,h3,h4,blockquote,pre,li"):
        text = clean(el.get_text(" ", strip=True))
        if text and len(text) >= 20 and not (len(text) < 500 and BAD_TEXT_RE.search(text)):
            if not blocks or blocks[-1] != text:
                blocks.append(text)
    if blocks:
        return "\n\n".join(blocks)
    text = clean(clone.get_text(" ", strip=True))
    return text or None


def likely_body(soup: BeautifulSoup) -> tuple[Any, str | None]:
    candidates = []
    selectors = [
        '[itemprop="articleBody"]', '[data-article-body]',
        '[class*="article-body"]', '[class*="article-content"]',
        '[class*="story-body"]', '[class*="story-content"]',
        '[class*="news-body"]', '[class*="news-content"]',
        '[class*="post-content"]', '[class*="post-body"]',
        "article", '[role="article"]', "main",
    ]
    seen: set[int] = set()
    for selector in selectors:
        for el in soup.select(selector):
            ident = id(el)
            if ident in seen:
                continue
            seen.add(ident)
            text = clean_body(el)
            if not text or len(text) < 150:
                continue
            p_count = len(el.select("p"))
            score = p_count * 600 + min(len(text), 50000) / 6
            if el.select_one("h1,h2,h3"):
                score += 500
            candidates.append((score, el))
    if not candidates:
        return None, None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1], selectors[0] if candidates[0][1].has_attr("itemprop") else None


def unique_css(tag: Any, soup: BeautifulSoup) -> str | None:
    if not tag:
        return None
    for attr in ("data-testid", "data-qa", "data-cy"):
        value = tag.get(attr)
        if value:
            q = f'{tag.name}[{attr}="{value}"]'
            if len(soup.select(q)) == 1:
                return q
    if tag.get("id"):
        q = f'#{tag["id"]}'
        if len(soup.select(q)) == 1:
            return q
    parts = []
    node = tag
    for _ in range(8):
        if not node or not getattr(node, "name", None):
            break
        part = node.name
        classes = [c for c in node.get("class", []) if re.match(r"^[A-Za-z_][A-Za-z0-9_-]{1,60}$", c) and c not in {"active", "selected", "show", "open", "hidden"}][:3]
        part += "".join("." + c.replace(".", "\\.") for c in classes)
        siblings = [x for x in getattr(node, "parent", []) if getattr(x, "name", None) == node.name] if node.parent else []
        if len(siblings) > 1:
            part += f":nth-of-type({siblings.index(node)+1})"
        parts.insert(0, part)
        q = " > ".join(parts)
        try:
            if len(soup.select(q)) == 1:
                return q
        except Exception:
            pass
        node = node.parent
    return " > ".join(parts) if parts else None


def field_from_static(soup: BeautifulSoup, field: dict[str, Any]) -> tuple[FieldPlan, Any]:
    path = field["path"]
    name = field["name"]
    lower = path.lower()

    # Structured-data first for common fields.
    common_jsonld = {
        "date": "datePublished",
        "published_date": "datePublished",
        "published_at": "datePublished",
        "author": "author.name",
        "title": "headline",
        "image": "image",
    }
    for suffix, jpath in common_jsonld.items():
        if lower.endswith(suffix):
            value = jsonld_find(soup, jpath)
            if value not in (None, ""):
                return FieldPlan(name=name, path=path, required=field["required"], value_type=field["value_type"], selector=jpath, selector_type="jsonld", found=True, value=clean(value), confidence=.97, evidence=f"JSON-LD: {jpath}"), value

    meta_map = {
        "image": [('meta[property="og:image"]', "content"), ('meta[name="twitter:image"]', "content")],
        "description": [('meta[name="description"]', "content"), ('meta[property="og:description"]', "content")],
        "title": [('meta[property="og:title"]', "content"), ('meta[name="twitter:title"]', "content")],
        "date": [('meta[property="article:published_time"]', "content"), ('meta[itemprop="datePublished"]', "content")],
        "author": [('meta[name="author"]', "content"), ('meta[property="article:author"]', "content")],
    }
    for key, pairs in meta_map.items():
        if lower.endswith(key):
            for sel, attr in pairs:
                value = meta_value(soup, sel, attr)
                if value not in (None, ""):
                    return FieldPlan(name=name, path=path, required=field["required"], value_type=field["value_type"], selector=sel, selector_type="meta", attribute=attr, found=True, value=clean(value), confidence=.99, evidence=f"meta: {sel}"), value

    if lower.endswith("title"):
        for sel in ("h1", '[itemprop="headline"]'):
            tag = soup.select_one(sel)
            value = clean(tag.get_text(" ", strip=True) if tag else None)
            if value:
                q = unique_css(tag, soup) or sel
                return FieldPlan(name=name, path=path, required=field["required"], value_type=field["value_type"], selector=q, selector_type="css", found=True, value=value, confidence=.9, evidence=f"heading selector: {q}"), value

    if lower.endswith("body") or lower.endswith("content") or lower.endswith("text"):
        root, fallback = likely_body(soup)
        if root:
            q = fallback or unique_css(root, soup)
            value = clean_body(root)
            if q and value:
                return FieldPlan(name=name, path=path, required=field["required"], value_type=field["value_type"], selector=q, selector_type="css", found=True, value=value, confidence=.94, evidence=f"body container: {q}"), value

    # Explicit common tabular/text attributes.
    text = clean(soup.get_text(" ", strip=True)) or ""
    if lower.endswith("view_count"):
        m = re.search(r"(?:بازدید|views?)\s*[:：]?\s*([\d۰-۹,٬]+)", text, re.I)
        if m:
            value = m.group(1)
            return FieldPlan(name=name, path=path, required=field["required"], value_type=field["value_type"], selector=None, selector_type="text", found=True, value=value, confidence=.82, evidence="page text matched view count"), value
    if lower.endswith("news_code"):
        m = re.search(r"(?:کد خبر|news\s*code)\s*[:：]?\s*([\d۰-۹]+)", text, re.I)
        if m:
            value = m.group(1)
            return FieldPlan(name=name, path=path, required=field["required"], value_type=field["value_type"], selector=None, selector_type="text", found=True, value=value, confidence=.86, evidence="page text matched news code"), value

    return FieldPlan(name=name, path=path, required=field["required"], value_type=field["value_type"], found=False, value=None, confidence=0), None


class StaticExtractor:
    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout

    async def fetch(self, url: str) -> tuple[str, str, int, str]:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; ResearchCrawler/1.0)"}
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, headers=headers) as client:
            r = await client.get(url)
            return r.url.__str__(), r.text, r.status_code, r.headers.get("content-type", "")

    async def inspect(self, url: str, contract: list[dict[str, Any]]) -> dict[str, Any]:
        final_url, html, status, content_type = await self.fetch(url)
        soup = BeautifulSoup(html, "html.parser")
        visible_text = clean(soup.get_text(" ", strip=True)) or ""
        challenge = bool(CHALLENGE_RE.search(html[:500000]) or CHALLENGE_RE.search(visible_text[:20000]))
        scripts = len(soup.find_all("script"))
        paragraphs = len(soup.select("p"))
        static_score = 0
        if status < 400:
            static_score += 1
        if "html" in content_type.lower():
            static_score += 1
        if len(visible_text) > 1000:
            static_score += 2
        if paragraphs >= 2:
            static_score += 1
        if scripts > 0 and len(visible_text) < 500:
            static_score -= 2
        fields: list[FieldPlan] = []
        data: dict[str, Any] = {}
        missing: list[str] = []
        for field in contract:
            fp, value = field_from_static(soup, field)
            fields.append(fp)
            parts = field["path"].split(".")
            cur = data
            for part in parts[:-1]:
                cur = cur.setdefault(part, {})
            cur[parts[-1]] = value
            if field["required"] and value in (None, ""):
                missing.append(field["path"])
        usable = not challenge and status < 400 and not missing
        return {
            "url": final_url,
            "status_code": status,
            "page_title": clean(soup.title.get_text() if soup.title else "") or "",
            "html": html,
            "content_type": content_type,
            "challenge": challenge,
            "static_score": static_score,
            "fields": fields,
            "data": data,
            "missing": missing,
            "usable": usable,
            "notes": [f"static_score={static_score}", f"paragraphs={paragraphs}", f"scripts={scripts}"],
        }
