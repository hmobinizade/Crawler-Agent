from __future__ import annotations

import json
import pprint
import re
from pathlib import Path
from textwrap import dedent
from .models import DiscoveryResult

COMMON_HELPERS = r'''
import json, os, re, time
from pathlib import Path
from urllib.parse import urlparse, urljoin

ROOT = os.getenv('CRAWLER_ROOT', __ROOT__)
START_URL = os.getenv('CRAWLER_START_URL', __START_URL__)
OUT = Path(os.getenv('CRAWLER_OUT', __OUT__))
FIELDS = __FIELDS__
REQUIRED = __REQUIRED__
MIN_DELAY_SECONDS = 1.5
MAX_RETRIES = 3
REQUEST_TIMEOUT = 30
NAV_TIMEOUT_MS = 60000


def clean_text(value):
    if value is None:
        return None
    value = re.sub(r"\s+", " ", str(value)).strip()
    return value or None


def normalize_digits(value):
    if value is None:
        return None
    return str(value).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")).replace(",", "")


def set_path(obj, path, value):
    cur = obj
    parts = path.split('.')
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def get_path(obj, path):
    cur = obj
    for part in path.split('.'):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def missing_required(data):
    return [p for p in REQUIRED if get_path(data, p) in (None, '', [])]


def same_site(url, root=ROOT):
    return urlparse(url).netloc.lower() == urlparse(root).netloc.lower()


def _page_pattern(root=ROOT):
    path=urlparse(root).path or '/'
    parts=[p for p in path.split('/') if p]
    if not parts:
        return re.compile(r'^/$')
    out=[]
    for part in parts:
        if re.fullmatch(r'\d+', part):
            out.append(r'\d+')
        elif len(part) >= 18 and re.fullmatch(r'[A-Za-z0-9_-]+', part):
            out.append(r'[^/]+')
        elif len(part) >= 7 and any(ch.isdigit() for ch in part) and re.fullmatch(r'[A-Za-z0-9_-]+', part):
            out.append(r'[^/]+')
        else:
            out.append(re.escape(part))
    return re.compile(r'^/' + '/'.join(out) + r'/?$')


PAGE_PATH_RE = _page_pattern()


def same_page_type(url, root=ROOT):
    if not same_site(url, root):
        return False
    return bool(PAGE_PATH_RE.match(urlparse(url).path or '/'))


def candidate_selectors(spec):
    out=[]
    for value in [spec.get('selector'), *(spec.get('selector_candidates') or [])]:
        if value and value not in out:
            out.append(value)
    return out
'''

STATIC_TEMPLATE = COMMON_HELPERS + r'''
import requests
from bs4 import BeautifulSoup
try:
    from lxml import html as lxml_html
except Exception:
    lxml_html = None
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def build_session():
    session = requests.Session()
    retry = Retry(
        total=MAX_RETRIES,
        connect=MAX_RETRIES,
        read=MAX_RETRIES,
        status=MAX_RETRIES,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'fa-IR,fa;q=0.9,en-US;q=0.8,en;q=0.7',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    })
    return session


def fetch(session, url):
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    if not response.encoding:
        response.encoding = response.apparent_encoding
    return response


def jsonld_objects(soup):
    objects=[]
    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            payload = json.loads(raw.strip())
        except Exception:
            continue
        objects.extend(payload if isinstance(payload, list) else [payload])
    return [x for x in objects if isinstance(x, dict)]


def jsonld_get_raw(soup, path):
    for obj in jsonld_objects(soup):
        cur = obj
        for part in str(path).split('.'):
            if isinstance(cur, dict):
                cur = cur.get(part)
            else:
                cur = None
                break
        if cur not in (None, ''):
            return cur
    return None


def _jsonld_scalar(value):
    if value in (None, ''):
        return None
    if isinstance(value, dict):
        return value.get('name') or value.get('url') or value.get('@id') or value.get('value')
    if isinstance(value, list):
        return [_jsonld_scalar(x) for x in value]
    return clean_text(value)


def jsonld_get(soup, path):
    value = jsonld_get_raw(soup, path)
    if value in (None, ''):
        return None
    if isinstance(value, list):
        return [_jsonld_scalar(x) for x in value]
    return _jsonld_scalar(value)

def meta_value(soup, key):
    key=key.lower()
    for el in soup.select('meta[content]'):
        candidate=' '.join(filter(None,[el.get('property'),el.get('name'),el.get('itemprop')])).lower()
        if candidate == key:
            return clean_text(el.get('content'))
    return None


def clean_body(root):
    clone=BeautifulSoup(str(root), 'html.parser')
    bad=re.compile(r'(^|[-_\s])(ad|ads|advert|advertisement|banner|promo|sponsor|sponsored|related|recommend|recommended|comments?|comment-section|share|sharing|social|newsletter|subscribe|cookie|popup|modal|sidebar|breadcrumb|toolbar|navigation)([-_\s]|$)', re.I)
    bad_text=re.compile(r'(advertisement|تبلیغ|آگهی|مطالب مرتبط|اخبار مرتبط|دیدگاه|نظرات کاربران|اشتراک گذاری|عضویت|خبرهای مرتبط|پیشنهاد سردبیر|مطالب پیشنهادی)', re.I)
    for e in clone.select('script,style,noscript,iframe,svg,form'):
        e.decompose()
    for e in list(clone.select('*')):
        attrs=getattr(e,'attrs',None) or {}
        meta=' '.join([str(attrs.get('id','')), ' '.join(attrs.get('class',[])) if isinstance(attrs.get('class'),list) else str(attrs.get('class','')), str(attrs.get('aria-label','')), str(attrs.get('role','')), str(attrs.get('data-ad',''))])
        t=clean_text(e.get_text(' ',strip=True)) or ''
        leaf = not bool(e.find(recursive=False))
        safe_text_block = e.name in {'p','li','a','span','button'} or leaf
        if bad.search(meta) or (safe_text_block and len(t)<350 and bad_text.search(t)):
            e.decompose()
    blocks=[]
    for e in clone.select('p,h2,h3,h4,blockquote,pre,li'):
        t=clean_text(e.get_text(' ',strip=True))
        if t and len(t)>=20 and not (len(t)<500 and bad_text.search(t)):
            if not blocks or blocks[-1] != t:
                blocks.append(t)
    return '\n\n'.join(blocks) if blocks else clean_text(clone.get_text(' ',strip=True))


def article_root(soup, preferred=None):
    selectors=[]
    if preferred:
        selectors.extend(preferred if isinstance(preferred,list) else [preferred])
    selectors += [
        '[itemprop="articleBody"]','[data-article-body]','[class*="article-body"]','[class*="article-content"]',
        '[class*="story-body"]','[class*="story-content"]','[class*="news-body"]','[class*="news-content"]',
        '[class*="post-content"]','[class*="post-body"]','article[role="article"]','article','main'
    ]
    best=None; best_score=-1
    for selector in selectors:
        try: nodes=soup.select(selector)
        except Exception: nodes=[]
        for node in nodes:
            txt=clean_body(node) or ''
            if len(txt)<80: continue
            pcount=len(node.select('p'))
            score=pcount*600 + min(len(txt),50000)/6
            if score>best_score:
                best,best_score=node,score
    return best


def extract_news_code(soup):
    for selector, attr in [('meta[property="og:url"]','content'),('link[rel="canonical"]','href')]:
        el=soup.select_one(selector)
        if el:
            m=re.search(r'/fa/news/(\d+)', el.get(attr,'') or '')
            if m: return m.group(1)
    text=soup.get_text(' ',strip=True)
    m=re.search(r'کد خبر\s*[:：]?\s*([0-9۰-۹]+)', text)
    return normalize_digits(m.group(1)) if m else None


def extract_title(soup):
    for selector in ['h1.Htag','h1[itemprop="headline"]','h1','.article-title','.news-title']:
        el=soup.select_one(selector)
        if el:
            value=clean_text(el.get_text(' ',strip=True))
            if value: return value
    return jsonld_get(soup,'headline') or meta_value(soup,'og:title') or clean_text(soup.title.get_text()) if soup.title else None


def extract_image(soup):
    for selector, attr in [
        ('meta[property="og:image"]','content'),
        ('meta[name="twitter:image"]','content'),
        ('img.lead_image.img-fluid.img-responsive-news','src'),
        ('[itemprop="image"]','src'),
    ]:
        el=soup.select_one(selector)
        if el:
            value=clean_text(el.get(attr))
            if value: return value
    raw=jsonld_get_raw(soup,'image')
    if isinstance(raw,list) and raw: raw=raw[0]
    if isinstance(raw,dict): raw=raw.get('url')
    return clean_text(raw)


def extract_date(soup):
    for key in ['article:published_time','datePublished','date','dc.date','pubdate']:
        if ':' in key or key in {'date','dc.date','pubdate'}:
            value=meta_value(soup,key)
        else:
            value=jsonld_get(soup,key)
        if value: return value
    return None


def extract_author(soup):
    for key in ['author','article:author','dc.creator']:
        value=meta_value(soup,key)
        if value: return value
    value=jsonld_get(soup,'author')
    if value: return value
    for selector in ['[rel="author"]','[itemprop="author"]','.author','.byline','[class*="author"]']:
        el=soup.select_one(selector)
        if el:
            value=clean_text(el.get_text(' ',strip=True))
            if value: return value
    return None


def extract_view_count(soup):
    roots=[soup.select_one('main.container.night_mode_news'), soup]
    for root in roots:
        if not root: continue
        text=root.get_text(' ',strip=True)
        m=re.search(r'([\d۰-۹,]+)\s*(?:بازدید|views?)', text, re.I)
        if m: return normalize_digits(m.group(1))
    return None


def extract_topic(soup, preferred=None):
    root=article_root(soup, preferred)
    if not root: return None
    text=root.get_text('\n',strip=True)
    m=re.search(r'([^\n]+»[^\n]+)', text)
    return clean_text(m.group(1)) if m else None


def extract_text_value(soup, text_value, scope_selector=None):
    text_value = clean_text(text_value)
    if not text_value:
        return None
    root = soup.select_one(scope_selector) if scope_selector else soup
    if not root:
        return None
    candidates = root.find_all(string=lambda node: text_value in clean_text(node) if clean_text(node) else False)
    for node in candidates:
        parent = node.parent
        if parent:
            value = clean_text(parent.get_text(' ', strip=True))
            if value:
                return value
    return text_value if text_value in root.get_text(' ', strip=True) else None


def _hybrid_jsonld_candidate(soup, candidate):
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

def postprocess_exact_value(spec, value):
    if value in (None, '', []):
        return value
    joined = ' '.join([
        str(spec.get('name','')),
        str(spec.get('path','')),
        str(spec.get('extraction_hint','')),
    ]).lower()
    if re.search(r'news[_\s-]*code|article[_\s-]*id|news[_\s-]*id', joined):
        m = re.search(r'/fa/news/(\d+)', str(value))
        if m:
            return m.group(1)
        m = re.search(r'/([^/?#]+)/?$', str(value))
        if m and re.fullmatch(r'[0-9۰-۹]+', m.group(1)):
            return normalize_digits(m.group(1))
        m = re.search(r'کد خبر\s*[:：]?\s*([0-9۰-۹]+)', str(value))
        if m:
            return normalize_digits(m.group(1))
    if re.search(r'view[_\s-]*count|views?|بازدید', joined):
        m = re.search(r'([0-9۰-۹,]+)', str(value))
        return normalize_digits(m.group(1)) if m else clean_text(value)
    return value


def extract_by_exact_spec(soup, spec):
    """Execute the exact ExtractionSpec produced during analysis before fallbacks."""
    selector_type = spec.get('selector_type') or 'none'
    selectors = candidate_selectors(spec)
    attribute = spec.get('attribute')

    if spec.get('value_type') == 'array':
        return extract_collection(soup, spec)

    if selector_type == 'jsonld':
        for path in selectors:
            value = jsonld_get(soup, path)
            if value not in (None, ''):
                return postprocess_exact_value(spec, value)
        return None

    if selector_type in {'meta', 'attribute'}:
        for selector in selectors:
            try:
                node = soup.select_one(selector)
            except Exception:
                node = None
            if not node:
                continue
            attr = attribute or ('content' if selector_type == 'meta' else None)
            value = node.get(attr) if attr else node.get_text(' ', strip=True)
            value = clean_text(value)
            if value not in (None, ''):
                return postprocess_exact_value(spec, value)
        return None

    if selector_type == 'css':
        path = str(spec.get('path', '')).lower()
        hint = str(spec.get('extraction_hint', '')).lower()
        body_like = bool(re.search(
            r'(^|[._\s-])(body|full[_\s-]*text|article[_\s-]*text|content)(?:$|[._\s-])',
            path,
        )) or bool(re.search(r'body|full text|article body|متن', hint))
        for selector in selectors:
            try:
                if body_like:
                    root = soup.select_one(selector)
                    if root:
                        value = clean_body(root)
                        if value:
                            return value
                else:
                    nodes = soup.select(selector)
                    if not nodes:
                        continue
                    node = nodes[0]
                    value = node.get(attribute) if attribute else node.get_text(' ', strip=True)
                    value = clean_text(value)
                    if value not in (None, ''):
                        return postprocess_exact_value(spec, value)
            except Exception:
                continue
        return None

    if selector_type == 'text':
        value = extract_text_value(soup, spec.get('selector'), spec.get('scope_selector'))
        if value not in (None, ''):
            return postprocess_exact_value(spec, value)
        return None

    return None


def extract_field(soup, spec):
    # 1) The verified/analyzed ExtractionSpec is authoritative.
    exact = extract_by_exact_spec(soup, spec)
    if exact not in (None, '', []):
        return exact

    # 2) Only after exact execution fails, use deterministic semantic fallbacks.
    name = spec.get('name','').lower()
    path = str(spec.get('path','')).lower()
    hint = str(spec.get('extraction_hint','')).lower()
    joined = ' '.join([name, path, hint])

    if spec.get('value_type') == 'array':
        return extract_collection(soup, spec)
    if re.search(r'news[_\s-]*code|article[_\s-]*id|news[_\s-]*id', joined):
        return extract_news_code(soup)
    if re.search(r'view[_\s-]*count|views?|بازدید', joined):
        return extract_view_count(soup)
    if re.search(r'(^|[._\s-])image|photo|thumbnail|picture|تصویر', joined):
        return extract_image(soup)
    if re.search(r'(^|[._\s-])title$|headline|عنوان', joined):
        return extract_title(soup)
    if re.search(r'author|byline|نویسنده', joined):
        return extract_author(soup)
    if re.search(r'date|published|publish|تاریخ', joined):
        return extract_date(soup)

    body_like = bool(re.search(r'body|full[_\s-]*text|article[_\s-]*text|content|متن', joined))
    if body_like:
        root = article_root(soup, None)
        return clean_body(root) if root else None
    return None




def _scope_root(soup, scope_selector):
    if not scope_selector:
        return soup
    try:
        return soup.select_one(scope_selector)
    except Exception:
        return None


def _xpath_values(soup, expression, scope_selector=None):
    if lxml_html is None:
        return []
    try:
        scope = _scope_root(soup, scope_selector)
        tree = lxml_html.fromstring(str(scope if scope is not None else soup))
        return tree.xpath(expression)
    except Exception:
        return []


def _xpath_value(item):
    if item is None:
        return None
    if isinstance(item, str):
        return clean_text(item)
    if hasattr(item, 'text_content'):
        return clean_text(item.text_content())
    if hasattr(item, 'keys'):
        try:
            return json.dumps(dict(item), ensure_ascii=False)
        except Exception:
            pass
    return clean_text(item)


def _jsonld_typed(soup, path, value_type='string'):
    raw = jsonld_get_raw(soup, path)
    if raw in (None, ''):
        return None
    if value_type == 'array':
        values = raw if isinstance(raw, list) else [raw]
        return values
    if value_type == 'object':
        return raw if isinstance(raw, dict) else None
    if value_type == 'number':
        if isinstance(raw, (int, float)):
            return raw
        m = re.search(r'-?\d+(?:\.\d+)?', str(raw))
        return float(m.group()) if m and '.' in m.group() else (int(m.group()) if m else None)
    if value_type == 'boolean':
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        return True if text in {'true','1','yes'} else False if text in {'false','0','no'} else None
    if isinstance(raw, dict):
        return raw.get('name') or raw.get('url') or raw.get('@id') or raw.get('value')
    if isinstance(raw, list):
        return raw[0] if raw else None
    return clean_text(raw)


def _text_matches(soup, text_value, scope_selector=None, many=False):
    text_value = clean_text(text_value)
    if not text_value:
        return None
    root = _scope_root(soup, scope_selector) or soup
    matches=[]
    for node in root.find_all(string=True):
        content=clean_text(node)
        if not content:
            continue
        if content == text_value or text_value in content:
            parent=node.parent
            value=clean_text(parent.get_text(' ', strip=True)) if parent else content
            if value and value not in matches:
                matches.append(value)
                if not many:
                    return value
    return matches if many and matches else None


def _extract_node_field(node, fs):
    if not isinstance(fs, dict):
        fs={'selector':str(fs),'selector_type':'css'}
    st=fs.get('selector_type') or 'css'; selector=fs.get('selector'); attr=fs.get('attribute')
    if st == 'text':
        return _text_matches(node, selector, fs.get('scope_selector'))
    if st == 'xpath':
        for item in _xpath_values(node, selector, fs.get('scope_selector')):
            value=_xpath_value(item)
            if value: return value
        return None
    try:
        child=node.select_one(selector) if selector else node
    except Exception:
        child=None
    if child is None:
        return None
    if attr:
        return clean_text(child.get(attr))
    return clean_text(child.get_text(' ', strip=True))


def _extract_collection_exact(soup, spec):
    st=spec.get('selector_type') or 'none'
    if st == 'jsonld':
        for path in candidate_selectors(spec):
            value=_jsonld_typed(soup,path,'array')
            if value not in (None,[]):
                return value
        return None
    if st == 'text':
        value=_text_matches(soup,spec.get('selector'),spec.get('scope_selector'),many=True)
        return value or None
    if st in {'css','meta','attribute'}:
        scope=_scope_root(soup,spec.get('scope_selector')) or soup
        selectors=[]
        if spec.get('item_selector') and st == 'css': selectors.append(spec.get('item_selector'))
        selectors.extend(candidate_selectors(spec))
        for selector in selectors:
            try: nodes=scope.select(selector)
            except Exception: nodes=[]
            if not nodes: continue
            fields=spec.get('item_fields') or {}
            if fields:
                rows=[]
                for node in nodes[:500]:
                    row={k:_extract_node_field(node,fs) for k,fs in fields.items()}
                    if any(v not in (None,'',[]) for v in row.values()): rows.append(row)
                if rows: return rows
            values=[]
            for node in nodes:
                attr=spec.get('attribute') or ('content' if st=='meta' else None)
                value=clean_text(node.get(attr)) if attr else clean_text(node.get_text(' ',strip=True))
                if value: values.append(value)
            if values: return values
    if st == 'xpath':
        values=[]
        for expression in [spec.get('item_selector'), *candidate_selectors(spec)]:
            if not expression: continue
            for node in _xpath_values(soup,expression,spec.get('scope_selector')):
                value=_xpath_value(node)
                if value: values.append(value)
        return values or None
    return None


def _extract_exact_v17(soup, spec):
    st=spec.get('selector_type') or 'none'; selectors=candidate_selectors(spec); attr=spec.get('attribute')
    if spec.get('value_type') == 'array':
        return _extract_collection_exact(soup,spec)
    if st == 'jsonld':
        for path in selectors:
            value=_jsonld_typed(soup,path,spec.get('value_type','string'))
            if value not in (None,'',[]): return postprocess_exact_value(spec,value)
        return None
    if st == 'text':
        value=_text_matches(soup,spec.get('selector'),spec.get('scope_selector'))
        return postprocess_exact_value(spec,value) if value else None
    if st in {'css','meta','attribute'}:
        scope=_scope_root(soup,spec.get('scope_selector')) or soup
        path=str(spec.get('path','')).lower(); hint=str(spec.get('extraction_hint','')).lower()
        body_like=bool(re.search(r'(body|full[_\s-]*text|article[_\s-]*text|content)',path)) or bool(re.search(r'body|full text|article body|متن',hint))
        for selector in selectors:
            try: nodes=scope.select(selector)
            except Exception: nodes=[]
            if not nodes: continue
            node=nodes[0]
            if attr or st in {'meta','attribute'}:
                attr_name=attr or ('content' if st=='meta' else None)
                value=clean_text(node.get(attr_name)) if attr_name else clean_text(node.get_text(' ',strip=True))
            else:
                value=clean_body(node) if body_like else clean_text(node.get_text(' ',strip=True))
            if value not in (None,''): return postprocess_exact_value(spec,value)
        return None
    if st == 'xpath':
        for expression in selectors:
            for item in _xpath_values(soup,expression,spec.get('scope_selector')):
                value=_xpath_value(item)
                if value not in (None,''): return postprocess_exact_value(spec,value)
        return None
    return None


def extract_field_v17(soup,spec,trace):
    selectors=candidate_selectors(spec)
    exact=_extract_exact_v17(soup,spec)
    entry={'path':spec.get('path'),'selector_type':spec.get('selector_type'),'selector':spec.get('selector'),'selector_candidates':selectors,'scope_selector':spec.get('scope_selector'),'attempts':[{'stage':'exact_spec','success':exact not in (None,'',[])}],'fallback_used':False}
    if exact not in (None,'',[]):
        entry['result']='exact'; trace.append(entry); return exact
    name=spec.get('name','').lower(); path=str(spec.get('path','')).lower(); hint=str(spec.get('extraction_hint','')).lower(); joined=' '.join([name,path,hint])
    fallback=None
    if re.search(r'news[_\s-]*code|article[_\s-]*id|news[_\s-]*id',joined): fallback=extract_news_code(soup)
    elif re.search(r'view[_\s-]*count|views?|بازدید',joined): fallback=extract_view_count(soup)
    elif re.search(r'(^|[._\s-])image|photo|thumbnail|picture|تصویر',joined): fallback=extract_image(soup)
    elif re.search(r'(^|[._\s-])title$|headline|عنوان',joined): fallback=extract_title(soup)
    elif re.search(r'author|byline|نویسنده',joined): fallback=extract_author(soup)
    elif re.search(r'date|published|publish|تاریخ',joined): fallback=extract_date(soup)
    elif re.search(r'body|full[_\s-]*text|article[_\s-]*text|content|متن',joined):
        root=article_root(soup,None); fallback=clean_body(root) if root else None
    entry['fallback_used']=fallback not in (None,'',[]); entry['result']='fallback' if entry['fallback_used'] else 'missing'; trace.append(entry)
    return fallback


def extract_with_trace(soup):
    data={}; trace=[]
    for field in FIELDS:
        set_path(data,field['path'],extract_field_v17(soup,field,trace))
    return data,trace

def extract(soup):
    return extract_with_trace(soup)[0]

def challenge_detected(soup):
    text=soup.get_text(' ',strip=True)[:6000]
    return bool(re.search(r'captcha|verify you are human|unusual traffic|access denied|cloudflare',text,re.I))


def extract_url(url):
    session=build_session()
    try:
        response=fetch(session,url)
        soup=BeautifulSoup(response.text,'html.parser')
        data, extraction_trace=extract_with_trace(soup)
        missing=missing_required(data)
        result={'url':response.url,'status_code':response.status_code,'data':data,'missing_required':missing,'extraction_ok':response.status_code<400 and not missing,'method':'requests_bs4','extraction_trace':extraction_trace}
        print(json.dumps(result,ensure_ascii=False)); return result
    finally:
        session.close()


def candidate_links(soup,current_url):
    seen=set()
    for element in soup.select('a[href]'):
        href=element.get('href')
        if not href: continue
        full=urljoin(current_url,href).split('#',1)[0]
        if same_page_type(full,ROOT) and full not in seen:
            seen.add(full); yield full


def crawl():
    OUT.parent.mkdir(parents=True,exist_ok=True)
    session=build_session(); queue=[START_URL]; visited=set()
    try:
        with OUT.open('w',encoding='utf-8') as f:
            while queue and len(visited)<int(os.getenv('CRAWLER_MAX_PAGES','500')):
                url=queue.pop(0)
                if url in visited or not same_site(url): continue
                visited.add(url)
                try: response=fetch(session,url)
                except requests.RequestException: continue
                soup=BeautifulSoup(response.text,'html.parser')
                data, extraction_trace=extract_with_trace(soup); missing=missing_required(data)
                record={'url':response.url,'data':data,'missing_required':missing,'extraction_ok':response.status_code<400 and not missing,'method':'requests_bs4','extraction_trace':extraction_trace}
                f.write(json.dumps(record,ensure_ascii=False)+'\n'); f.flush()
                if not challenge_detected(soup):
                    for href in candidate_links(soup,response.url):
                        if href not in visited and len(queue)<500 and same_page_type(href,ROOT): queue.append(href)
                time.sleep(MIN_DELAY_SECONDS)
    finally:
        session.close()


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(); parser.add_argument('--url',default=START_URL); parser.add_argument('--once',action='store_true'); args=parser.parse_args()
    extract_url(args.url) if args.once else crawl()
'''

PLAYWRIGHT_TEMPLATE = COMMON_HELPERS + r'''
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

USER_DATA_DIR=Path(__USER_DATA_DIR__)


def scroll_to_bottom(page):
    page.evaluate("""async () => {
      const sleep=ms=>new Promise(r=>setTimeout(r,ms));
      let last=0,stable=0;
      for(let i=0;i<24;i++){
        window.scrollTo(0,document.documentElement.scrollHeight);
        await sleep(180);
        const h=Math.max(document.body?.scrollHeight||0,document.documentElement.scrollHeight||0);
        if(h===last) stable++; else stable=0;
        last=h;
        if(stable>=2) break;
      }
      window.scrollTo(0,0); await sleep(150);
    }""")

def postprocess_exact_value(spec, value):
    if value in (None, '', []):
        return value
    joined = ' '.join([
        str(spec.get('name','')),
        str(spec.get('path','')),
        str(spec.get('extraction_hint','')),
    ]).lower()
    if re.search(r'news[_\s-]*code|article[_\s-]*id|news[_\s-]*id', joined):
        m = re.search(r'/fa/news/(\d+)', str(value))
        if m:
            return m.group(1)
        m = re.search(r'/([^/?#]+)/?$', str(value))
        if m and re.fullmatch(r'[0-9۰-۹]+', m.group(1)):
            return normalize_digits(m.group(1))
        m = re.search(r'کد خبر\s*[:：]?\s*([0-9۰-۹]+)', str(value))
        if m:
            return normalize_digits(m.group(1))
    if re.search(r'view[_\s-]*count|views?|بازدید', joined):
        m = re.search(r'([0-9۰-۹,]+)', str(value))
        return normalize_digits(m.group(1)) if m else clean_text(value)
    return value


def clean_body_from_locator(locator):
    js=r"""root=>{
      const norm=s=>String(s??'').replace(/\u00a0/g,' ').replace(/\s+/g,' ').trim();
      const bad=/(^|[-_\s])(ad|ads|advert|advertisement|banner|promo|sponsor|sponsored|related|recommend|recommended|comments?|comment-section|share|sharing|social|newsletter|subscribe|cookie|popup|modal|sidebar|breadcrumb|toolbar|navigation)([-_\s]|$)/i;
      const badText=/(advertisement|تبلیغ|آگهی|مطالب مرتبط|اخبار مرتبط|دیدگاه|نظرات کاربران|اشتراک گذاری|عضویت|خبرهای مرتبط|پیشنهاد سردبیر|مطالب پیشنهادی)/i;
      if(!root) return null;
      const clone=root.cloneNode(true);
      for(const e of [...clone.querySelectorAll('script,style,noscript,iframe,svg,form')]) e.remove();
      for(const e of [...clone.querySelectorAll('*')]){
        const meta=[e.id||'',typeof e.className==='string'?e.className:'',e.getAttribute('aria-label')||'',e.getAttribute('role')||'',e.getAttribute('data-ad')||''].join(' ');
        const t=norm(e.innerText||'');
        if(bad.test(meta)||(t.length<350&&badText.test(t))) e.remove();
      }
      const blocks=[...clone.querySelectorAll('p,h2,h3,h4,blockquote,pre,li')].map(e=>norm(e.innerText||e.textContent||'')).filter(t=>t.length>=20 && !(t.length<500&&badText.test(t)));
      const out=[]; for(const t of blocks) if(!out.length||out[out.length-1]!==t) out.push(t);
      return out.length?out.join('\\n\\n'):(norm(clone.innerText||'')||null);
    }"""
    try: return locator.evaluate(js) or None
    except Exception: return None


def _jsonld_scalar_page(value):
    if value in (None, ''):
        return None
    if isinstance(value, dict):
        return value.get('name') or value.get('url') or value.get('@id') or value.get('value')
    return clean_text(value)


def jsonld_value(page,path):
    try: scripts=page.locator('script[type="application/ld+json"]')
    except Exception: return None
    for i in range(scripts.count()):
        try: payload=json.loads(scripts.nth(i).inner_text(timeout=3000))
        except Exception: continue
        for obj in (payload if isinstance(payload,list) else [payload]):
            cur=obj
            for part in str(path).split('.'):
                cur=cur.get(part) if isinstance(cur,dict) and cur.get(part, None) is not None else cur
            if cur not in (None,''):
                if isinstance(cur,list):
                    return [x if isinstance(x,dict) else _jsonld_scalar_page(x) for x in cur]
                return _jsonld_scalar_page(cur)
    return None


def meta_value(page,key):
    try: loc=page.locator('meta[content]')
    except Exception: return None
    key=key.lower()
    for i in range(loc.count()):
        node=loc.nth(i)
        candidate=' '.join(filter(None,[node.get_attribute('property'),node.get_attribute('name'),node.get_attribute('itemprop')])).lower()
        if candidate==key: return clean_text(node.get_attribute('content'))
    return None


def special_value(page,spec):
    joined=' '.join([str(spec.get('name','')),str(spec.get('path','')),str(spec.get('extraction_hint',''))]).lower()
    if re.search(r'news[_\s-]*code|article[_\s-]*id|news[_\s-]*id',joined):
        value=meta_value(page,'og:url')
        if value:
            m=re.search(r'/fa/news/(\d+)',value)
            if m:return m.group(1)
        try: text=clean_text(page.locator('body').inner_text(timeout=3000)) or ''
        except Exception: text=''
        m=re.search(r'کد خبر\s*[:：]?\s*([0-9۰-۹]+)',text)
        return normalize_digits(m.group(1)) if m else None
    if re.search(r'view[_\s-]*count|views?|بازدید',joined):
        try: text=clean_text(page.locator('body').inner_text(timeout=3000)) or ''
        except Exception: text=''
        m=re.search(r'([\d۰-۹,]+)\s*(?:بازدید|views?)',text,re.I)
        return normalize_digits(m.group(1)) if m else None
    if re.search(r'image|photo|thumbnail|picture|تصویر',joined):
        for key in ['og:image','twitter:image']:
            value=meta_value(page,key)
            if value:return value
        value=jsonld_value(page,'image') or jsonld_value(page,'thumbnailUrl')
        if value:return value
        for selector in ['img.lead_image.img-fluid.img-responsive-news','[itemprop="image"]','main img','article img']:
            try:
                node=page.locator(selector).first
                value=clean_text(node.get_attribute('src',timeout=1500) or node.get_attribute('data-src',timeout=500) or node.get_attribute('data-lazy-src',timeout=500))
                if value:return value
            except Exception: pass
    if re.search(r'(^|[._\s-])title$|headline|عنوان',joined):
        for selector in ['h1.Htag','h1[itemprop="headline"]','h1','article h1','.article-title','.news-title']:
            try:
                value=clean_text(page.locator(selector).first.inner_text(timeout=1500))
                if value:return value
            except Exception: pass
        return jsonld_value(page,'headline') or meta_value(page,'og:title')
    if re.search(r'author|byline|نویسنده',joined):
        for key in ['author','article:author','dc.creator']:
            value=meta_value(page,key)
            if value:return value
        value=jsonld_value(page,'author')
        if value:return value
        for selector in ['[rel="author"]','[itemprop="author"]','.author','.byline','[class*="author"]']:
            try:
                value=clean_text(page.locator(selector).first.inner_text(timeout=1500))
                if value:return value
            except Exception: pass
    if re.search(r'date|published|publish|تاریخ',joined):
        for key in ['article:published_time','datePublished','date','dc.date','pubdate']:
            value=meta_value(page,key) or jsonld_value(page,key)
            if value:return value
    return None


def extract_text_page(page, text_value, scope_selector=None):
    text_value = clean_text(text_value)
    if not text_value:
        return None
    try:
        root = page.locator(scope_selector) if scope_selector else page.locator('body')
        if root.count() == 0:
            return None
        # Exact text match first; then substring fallback.
        exact = root.get_by_text(text_value, exact=True).first
        if exact.count():
            return clean_text(exact.inner_text(timeout=2000))
        nodes = root.get_by_text(text_value, exact=False)
        if nodes.count():
            return clean_text(nodes.first.inner_text(timeout=2000))
    except Exception:
        return None
    return None


def _playwright_hybrid_candidate(page, candidate):
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

def extract_exact_page(page,spec):
    selector_type=spec.get('selector_type') or 'none'
    selectors=candidate_selectors(spec)
    attribute=spec.get('attribute')

    if spec.get('value_type') == 'array':
        return extract_collection(page,spec)

    if selector_type == 'jsonld':
        for selector in selectors:
            value=jsonld_value(page,selector)
            if value not in (None,''):
                return postprocess_exact_value(spec,value)
        return None

    if selector_type in {'meta','attribute'}:
        for selector in selectors:
            try:
                node=page.locator(selector).first
                if node.count() == 0: continue
                attr=attribute or ('content' if selector_type=='meta' else None)
                value=node.get_attribute(attr,timeout=2000) if attr else node.inner_text(timeout=2000)
                value=clean_text(value)
                if value not in (None,''):
                    return postprocess_exact_value(spec,value)
            except Exception: pass
        return None

    if selector_type == 'text':
        value=extract_text_page(page,spec.get('selector'),spec.get('scope_selector'))
        return postprocess_exact_value(spec,value) if value not in (None,'') else None

    if selector_type == 'css':
        path=str(spec.get('path','')).lower(); hint=str(spec.get('extraction_hint','')).lower()
        body_like=bool(re.search(r'(^|[._\s-])(body|full[_\s-]*text|article[_\s-]*text|content)(?:$|[._\s-])',path)) or bool(re.search(r'body|full text|article body|متن',hint))
        for selector in selectors:
            try:
                loc=page.locator(selector)
                if loc.count()==0: continue
                value=clean_body_from_locator(loc.first) if body_like else (loc.first.get_attribute(attribute,timeout=2000) if attribute else loc.first.inner_text(timeout=2000))
                value=clean_text(value) if not isinstance(value,list) else value
                if value not in (None,''):
                    return postprocess_exact_value(spec,value)
            except Exception: continue
        return None
    return None


def extract_field(page,spec):
    # The analyzed ExtractionSpec is authoritative. Fallbacks only run after it fails.
    exact=extract_exact_page(page,spec)
    if exact not in (None,'',[]):
        return exact

    special=special_value(page,spec)
    if special not in (None,''):
        return special

    name=spec.get('name','').lower(); path=str(spec.get('path','')).lower(); hint=str(spec.get('extraction_hint','')).lower()
    joined=' '.join([name,path,hint])
    if spec.get('value_type')=='array': return extract_collection(page,spec)
    if re.search(r'news[_\s-]*code|article[_\s-]*id|news[_\s-]*id',joined):
        value=special_value(page,{'name':'news_code','path':'news_code','extraction_hint':'news code'})
        return value
    if re.search(r'view[_\s-]*count|views?|بازدید',joined): return special_value(page,{'name':'view_count','path':'view_count','extraction_hint':'view count'})
    if re.search(r'image|photo|thumbnail|picture|تصویر',joined): return special_value(page,{'name':'image','path':'image','extraction_hint':'image'})
    if re.search(r'title$|headline|عنوان',joined): return special_value(page,{'name':'title','path':'title','extraction_hint':'title'})
    if re.search(r'author|byline|نویسنده',joined): return special_value(page,{'name':'author','path':'author','extraction_hint':'author'})
    if re.search(r'date|published|publish|تاریخ',joined): return special_value(page,{'name':'date','path':'date','extraction_hint':'date'})
    body_like=bool(re.search(r'body|full[_\s-]*text|article[_\s-]*text|content|متن',joined))
    if body_like:
        for selector in ['[itemprop="articleBody"]','[data-article-body]','[class*="article-body"]','[class*="article-content"]','[class*="story-body"]','[class*="news-body"]','article','main']:
            try:
                value=clean_body_from_locator(page.locator(selector).first)
                if value and len(value)>=150:return value
            except Exception:pass
    return None



def _locator_for(page, selector_type, selector, scope=None):
    if not selector:
        return None
    root = page.locator(scope).first if scope and selector_type in {'css','meta','attribute','text','xpath'} else page
    if selector_type == 'xpath': return root.locator(f'xpath={selector}')
    if selector_type in {'css','meta','attribute'}: return root.locator(selector)
    return None


def _jsonld_typed_page(page,path,value_type='string'):
    raw=None
    try: scripts=page.locator('script[type="application/ld+json"]')
    except Exception: return None
    for i in range(scripts.count()):
        try: payload=json.loads(scripts.nth(i).inner_text(timeout=3000))
        except Exception: continue
        for obj in (payload if isinstance(payload,list) else [payload]):
            cur=obj
            for part in str(path).split('.'):
                cur=cur.get(part) if isinstance(cur,dict) and cur.get(part, None) is not None else cur
            if cur not in (None,''):
                raw=cur; break
        if raw not in (None,''): break
    if raw in (None,''): return None
    if value_type=='array': return raw if isinstance(raw,list) else [raw]
    if value_type=='object': return raw if isinstance(raw,dict) else None
    if isinstance(raw,dict): return raw.get('name') or raw.get('url') or raw.get('@id') or raw.get('value')
    if isinstance(raw,list): return raw[0] if raw else None
    return clean_text(raw)


def _extract_text_page_many(page,text_value,scope=None):
    text_value=clean_text(text_value)
    if not text_value: return None
    try:
        root=page.locator(scope).first if scope else page.locator('body'); matches=root.get_by_text(text_value,exact=True)
        out=[]
        for i in range(matches.count()):
            value=clean_text(matches.nth(i).inner_text(timeout=2000))
            if value and value not in out: out.append(value)
        return out or None
    except Exception: return None


def _extract_item_field_page(item,fs):
    if not isinstance(fs,dict): fs={'selector':str(fs),'selector_type':'css'}
    st=fs.get('selector_type') or 'css'; selector=fs.get('selector'); attr=fs.get('attribute')
    try:
        if st=='text': loc=item.get_by_text(str(selector),exact=True).first
        elif st=='xpath': loc=item.locator(f'xpath={selector}').first
        else: loc=item.locator(selector).first if selector else item
        if loc.count()==0:return None
        if attr:return clean_text(loc.get_attribute(attr,timeout=2000))
        return clean_text(loc.inner_text(timeout=2000))
    except Exception:return None


def _extract_collection_page(page,spec):
    st=spec.get('selector_type') or 'none'
    if st=='jsonld':
        for path in candidate_selectors(spec):
            value=_jsonld_typed_page(page,path,'array')
            if value not in (None,[]): return value
        return None
    if st=='text': return _extract_text_page_many(page,spec.get('selector'),spec.get('scope_selector'))
    selectors=[]
    if spec.get('item_selector') and st in {'css','xpath'}: selectors.append(spec.get('item_selector'))
    selectors.extend(candidate_selectors(spec))
    for selector in selectors:
        try: loc=_locator_for(page,st,selector,spec.get('scope_selector'))
        except Exception: loc=None
        if not loc: continue
        try:
            if loc.count()==0: continue
        except Exception: continue
        fields=spec.get('item_fields') or {}
        if fields:
            rows=[]
            for i in range(min(loc.count(),500)):
                item=loc.nth(i); row={k:_extract_item_field_page(item,fs) for k,fs in fields.items()}
                if any(v not in (None,'',[]) for v in row.values()): rows.append(row)
            if rows:return rows
        values=[]
        for i in range(min(loc.count(),500)):
            node=loc.nth(i)
            try: value=clean_text(node.get_attribute(spec.get('attribute'),timeout=2000) if spec.get('attribute') else node.inner_text(timeout=2000))
            except Exception:value=None
            if value: values.append(value)
        if values:return values
    return None


def _extract_exact_page_v17(page,spec):
    st=spec.get('selector_type') or 'none'; selectors=candidate_selectors(spec); attr=spec.get('attribute')
    if spec.get('value_type')=='array': return _extract_collection_page(page,spec)
    if st=='jsonld':
        for path in selectors:
            value=_jsonld_typed_page(page,path,spec.get('value_type','string'))
            if value not in (None,'',[]): return postprocess_exact_value(spec,value)
        return None
    if st=='text':
        value=_extract_text_page_many(page,spec.get('selector'),spec.get('scope_selector'))
        value=value[0] if isinstance(value,list) and value else value
        return postprocess_exact_value(spec,value)
    if st in {'css','meta','attribute','xpath'}:
        for selector in selectors:
            try: loc=_locator_for(page,st,selector,spec.get('scope_selector'))
            except Exception: loc=None
            if not loc: continue
            try:
                if loc.count()==0: continue
                node=loc.first; path=str(spec.get('path','')).lower(); hint=str(spec.get('extraction_hint','')).lower()
                body_like=bool(re.search(r'(body|full[_\s-]*text|article[_\s-]*text|content)',path)) or bool(re.search(r'body|full text|article body|متن',hint))
                if body_like and st=='css': value=clean_body_from_locator(node)
                elif attr or st in {'meta','attribute'}: value=node.get_attribute(attr or ('content' if st=='meta' else None),timeout=2000)
                else: value=node.inner_text(timeout=2000)
                value=clean_text(value)
                if value not in (None,''):return postprocess_exact_value(spec,value)
            except Exception: continue
        return None
    return None


def extract_field_v17_page(page,spec,trace):
    exact=_extract_exact_page_v17(page,spec); entry={'path':spec.get('path'),'selector_type':spec.get('selector_type'),'selector':spec.get('selector'),'selector_candidates':candidate_selectors(spec),'scope_selector':spec.get('scope_selector'),'attempts':[{'stage':'exact_spec','success':exact not in (None,'',[])}],'fallback_used':False}
    if exact not in (None,'',[]):entry['result']='exact';trace.append(entry);return exact
    special=special_value(page,spec)
    if special not in (None,''):entry['fallback_used']=True;entry['result']='fallback';trace.append(entry);return special
    entry['result']='missing';trace.append(entry);return None

def extract_fields_page(page,spec,trace):
    exact=extract_exact_page(page,spec); entry={'path':spec.get('path'),'selector_type':spec.get('selector_type'),'selector':spec.get('selector'),'selector_candidates':candidate_selectors(spec),'scope_selector':spec.get('scope_selector'),'attempts':[{'stage':'exact_spec','success':exact not in (None,'',[])}],'fallback_used':False}
    if exact not in (None,'',[]):entry['result']='exact';trace.append(entry);return exact
    special=special_value(page,spec)
    if special not in (None,''):entry['fallback_used']=True;entry['result']='fallback';trace.append(entry);return special
    entry['result']='missing';trace.append(entry);return None

def extract_with_trace_page(page):
    data={};trace=[]
    for field in FIELDS:set_path(data,field['path'],extract_fields_page(page,field,trace))
    return data,trace

def extract_page(page):return extract_with_trace_page(page)[0]

def open_context():
    p=sync_playwright().start()
    context=p.chromium.launch_persistent_context(str(USER_DATA_DIR),headless=os.getenv('CRAWLER_HEADLESS','0').lower() in {'1','true','yes'},viewport={'width':1365,'height':900})
    page=context.pages[0] if context.pages else context.new_page()
    page.set_default_timeout(8000); page.set_default_navigation_timeout(NAV_TIMEOUT_MS)
    return p,context,page


def extract_url(url):
    p,context,page=open_context()
    try:
        for attempt in range(MAX_RETRIES):
            try:
                page.goto(url,wait_until='domcontentloaded'); page.wait_for_timeout(1200); scroll_to_bottom(page)
                data, extraction_trace=extract_with_trace_page(page); missing=missing_required(data)
                result={'url':page.url,'data':data,'missing_required':missing,'extraction_ok':not missing,'method':'playwright','extraction_trace':extraction_trace}
                print(json.dumps(result,ensure_ascii=False)); return result
            except PlaywrightTimeoutError:
                time.sleep(2**attempt)
        return None
    finally:
        context.close(); p.stop()


def candidate_links(page,current_url):
    seen=set()
    for href in page.locator('a[href]').evaluate_all("els => els.map(a => a.href).filter(Boolean)"):
        href=href.split('#',1)[0]
        if same_page_type(href,ROOT) and href not in seen:
            seen.add(href); yield href


def crawl():
    OUT.parent.mkdir(parents=True,exist_ok=True); queue=[START_URL]; visited=set(); p,context,page=open_context()
    try:
        with OUT.open('w',encoding='utf-8') as f:
            while queue and len(visited)<int(os.getenv('CRAWLER_MAX_PAGES','500')):
                url=queue.pop(0)
                if url in visited or not same_site(url):continue
                visited.add(url)
                try:
                    page.goto(url,wait_until='domcontentloaded'); page.wait_for_timeout(1200); scroll_to_bottom(page)
                except Exception:continue
                data, extraction_trace=extract_with_trace_page(page); missing=missing_required(data)
                record={'url':page.url,'data':data,'missing_required':missing,'extraction_ok':not missing,'method':'playwright','extraction_trace':extraction_trace}
                f.write(json.dumps(record,ensure_ascii=False)+'\n');f.flush()
                for href in candidate_links(page,page.url):
                    if href not in visited and len(queue)<500:queue.append(href)
                time.sleep(MIN_DELAY_SECONDS)
    finally:
        context.close();p.stop()


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--url',default=START_URL);parser.add_argument('--once',action='store_true');args=parser.parse_args()
    extract_url(args.url) if args.once else crawl()
'''


def generate(job_id: str, discovery: DiscoveryResult, out_dir: str='generated') -> list[str]:
    host=re.sub(r'[^a-zA-Z0-9._-]+','_',discovery.host) or 'unknown-host'
    folder=Path(out_dir)/host/f'job_{job_id}'
    folder.mkdir(parents=True,exist_ok=True)
    stem='09_crawler'
    fields=[f.model_dump() for f in discovery.fields]
    required=[f.path for f in discovery.fields if f.required]
    template=STATIC_TEMPLATE if discovery.method=='requests_bs4' else PLAYWRIGHT_TEMPLATE
    code=template.replace('__START_URL__',repr(discovery.url)).replace('__ROOT__',repr(discovery.url))
    code=code.replace('__OUT__',repr(str(folder/f'{stem}.jsonl'))).replace('__USER_DATA_DIR__',repr(str(folder/'.browser-profile')))
    code=code.replace('__FIELDS__',pprint.pformat(fields,width=120,sort_dicts=False)).replace('__REQUIRED__',pprint.pformat(required,width=120,sort_dicts=False))
    path=folder/f'{stem}.py'
    path.write_text(dedent(code),encoding='utf-8')
    schema=folder/'crawler.schema.json'
    schema.write_text(json.dumps(discovery.model_dump(),ensure_ascii=False,indent=2),encoding='utf-8')
    return [str(path),str(schema)]
