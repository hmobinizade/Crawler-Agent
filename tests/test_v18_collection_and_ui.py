from pathlib import Path


def test_collection_contract_is_present_in_crawler_runtime():
    source = Path('standalone/playwright_structure_crawler.py').read_text(encoding='utf-8')
    for token in ['item_fields', '@href', '@src', 'text', 'path', 'url']:
        assert token in source


def test_generated_page_uses_module_script():
    html = Path('frontend/static/generated.html').read_text(encoding='utf-8')
    assert 'type="module" src="/static/generated.js?v=22"' in html
    assert 'Artifact preview' in html


def test_crawler_page_accepts_url_and_structure():
    html = Path('frontend/static/crawler.html').read_text(encoding='utf-8')
    assert 'id="startUrl"' in html
    assert 'id="structure"' in html
    assert 'maxPages' not in html
    assert 'Max pages' not in html
    assert 'Execution mode' not in html


def test_crawl_request_is_single_page_contract():
    from app.models import CrawlRequest
    req = CrawlRequest(url="https://example.com/article/1", structure={"fields": []})
    assert not hasattr(req, "max_pages")
    assert req.timeout_seconds == 240


def test_crawler_page_has_no_max_pages_control():
    html = Path('frontend/static/crawler.html').read_text(encoding='utf-8')
    assert 'maxPages' not in html
    assert 'Max pages' not in html
    assert 'Record' in html
