from pathlib import Path
import importlib.util

from bs4 import BeautifulSoup
from app.models import DiscoveryResult, FieldPlan
from app.codegen import generate


def load_generated(path):
    spec = importlib.util.spec_from_file_location('generated_test_crawler', path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_collection_item_fields_support_href_and_text_and_candidates(tmp_path):
    html = '''
    <html><body>
      <a class="of-hidden line-clamp-2" href="https://farsnews.ir/a/123">خبر اول</a>
      <a class="of-hidden line-clamp-2" href="https://farsnews.ir/b/456">خبر دوم</a>
      <script type="application/ld+json">{"related":[{"url":"https://farsnews.ir/c/789"}]}</script>
    </body></html>
    '''
    discovery = DiscoveryResult(
        status='READY', host='farsnews.ir', url='https://farsnews.ir/a/1', method='requests_bs4', source='static',
        template={'news_urls': []}, proposed_json={'news_urls': ['https://farsnews.ir/a/123','https://farsnews.ir/b/456']},
        fields=[FieldPlan(
            name='news_urls', path='news_urls', description='Collection', required=True, value_type='array',
            selector='a.of-hidden.line-clamp-2', selector_type='css',
            selector_candidates=['a.of-hidden.line-clamp-2', "script[type='application/ld+json'] path related"],
            item_selector='a.of-hidden.line-clamp-2', item_fields={'url':'@href','title':'text'}, found=True,
            value=['https://farsnews.ir/a/123'], confidence=.9
        )]
    )
    paths = generate('collection_test', discovery, out_dir=str(tmp_path))
    module = load_generated(paths[0])
    soup = BeautifulSoup(html, 'html.parser')
    result = module.extract_collection(soup, discovery.fields[0].model_dump())
    assert result == [
        {'url':'https://farsnews.ir/a/123','title':'خبر اول'},
        {'url':'https://farsnews.ir/b/456','title':'خبر دوم'},
    ]


def test_generated_page_has_two_section_navigation():
    html = Path('frontend/static/generated.html').read_text(encoding='utf-8')
    assert 'href="/"' in html
    assert 'href="/generated"' in html
    assert 'Generated library' in html
    assert 'Artifact preview' in html


def test_generate_page_has_no_artifact_browser_panel():
    html = Path('frontend/static/index.html').read_text(encoding='utf-8')
    assert 'href="/generated"' in html
    assert 'Generated artifacts' not in html
