from pathlib import Path
import ast

from app.codegen import generate
from app.models import DiscoveryResult, FieldPlan


def discovery():
    return DiscoveryResult(
        status='READY',
        host='example.com',
        url='https://example.com/article/1',
        method='playwright',
        source='playwright',
        template={'title': '', 'date': '', 'body': ''},
        fields=[
            FieldPlan(name='title', path='title', selector='h1.article-title', selector_type='css', found=True, value='Title'),
            FieldPlan(name='date', path='date', selector='datePublished', selector_type='jsonld', found=True, value='2026-09-17'),
            FieldPlan(name='body', path='body', selector='article.article-body', selector_type='css', found=True, value='Body'),
        ],
        proposed_json={},
    )


def test_generated_crawler_is_playwright_only(tmp_path):
    paths = generate('quality', discovery(), out_dir=str(tmp_path))
    code = Path(paths[0]).read_text(encoding='utf-8')
    ast.parse(code)
    assert 'playwright' in code.lower()
    assert 'BeautifulSoup' not in code
    assert 'requests.Session' not in code
    assert 'httpx' not in code.lower()


def test_generated_crawler_contains_authoritative_structure(tmp_path):
    d = discovery()
    paths = generate('contract', d, out_dir=str(tmp_path))
    code = Path(paths[0]).read_text(encoding='utf-8')
    assert 'h1.article-title' in code
    assert 'datePublished' in code
    assert 'article.article-body' in code
    assert 'selector_candidates' in code
    assert 'FIELDS = ' in code


def test_generated_crawler_has_executable_entrypoint(tmp_path):
    paths = generate('entry', discovery(), out_dir=str(tmp_path))
    code = Path(paths[0]).read_text(encoding='utf-8')
    assert 'if __name__' in code
    assert '--once' in code
    assert 'missing_required' in code
    assert 'extraction_ok' in code
