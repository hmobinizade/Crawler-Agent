import json
from pathlib import Path

from app.models import CrawlRequest


def test_crawl_request_is_url_plus_structure():
    request = CrawlRequest.model_validate({
        'url':'https://example.com/article/1',
        'structure':{'execution':{'mode':'generic'},'fields':[]},
    })
    assert str(request.url).startswith('https://example.com/')
    assert request.structure['execution']['mode']=='generic'


def test_standalone_crawler_is_single_clean_file():
    path=Path('standalone/playwright_structure_crawler.py')
    source=path.read_text(encoding='utf-8')
    assert 'class ExtractionEngine' in source
    assert 'def run_structure' in source
    assert 'playwright' in source.lower()
    assert 'requests.Session' not in source
    assert 'BeautifulSoup' not in source


def test_structure_example_can_be_serialized(tmp_path):
    structure={'execution':{'mode':'generic'},'fields':[{'name':'title','path':'title','selector':'h1','selector_type':'css'}]}
    target=tmp_path/'structure.json'
    target.write_text(json.dumps(structure,ensure_ascii=False,indent=2),encoding='utf-8')
    assert json.loads(target.read_text(encoding='utf-8'))['fields'][0]['selector']=='h1'
