from pathlib import Path
import importlib.util

from bs4 import BeautifulSoup

from app.codegen import generate
from app.models import DiscoveryResult, FieldPlan


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location('generated_v17_exact', path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_scope_candidates_jsonld_and_text_contract(tmp_path):
    fields = [
        FieldPlan(name='date', path='date', required=True, selector='time.publish-date', selector_type='css', scope_selector='div.meta', found=True),
        FieldPlan(name='topic', path='topic', required=True, selector='Culture', selector_type='text', scope_selector='div.meta', found=True),
        FieldPlan(name='media', path='media', required=True, value_type='array', selector='image', selector_type='jsonld', found=True),
        FieldPlan(name='author', path='author', required=True, selector='author.name', selector_type='jsonld', found=True),
    ]
    d = DiscoveryResult(
        status='READY', host='example.com', url='https://example.com/article/1',
        method='requests_bs4', source='static', template={'date':'','topic':'','media':[],'author':''},
        fields=fields, proposed_json={}
    )
    files = generate('exact-contract', d, out_dir=str(tmp_path))
    mod = load_module(Path(files[0]))
    html = '''
    <html><head>
      <script type="application/ld+json">
      {"author":{"name":"Ada"},"image":["https://x/1.jpg","https://x/2.jpg"]}
      </script>
    </head><body>
      <div class="other"><time class="publish-date">WRONG</time></div>
      <div class="meta"><time class="publish-date">2026-09-17</time><span>Culture</span></div>
    </body></html>
    '''
    data, trace = mod.extract_with_trace(BeautifulSoup(html, 'html.parser'))
    assert data['date'] == '2026-09-17'
    assert data['topic'] == 'Culture'
    assert data['media'] == ['https://x/1.jpg', 'https://x/2.jpg']
    assert data['author'] == 'Ada'
    assert all(item['result'] == 'exact' for item in trace)
    assert all(item['fallback_used'] is False for item in trace)


def test_xpath_candidate_is_executed(tmp_path):
    fields = [
        FieldPlan(name='title', path='title', required=True, selector='//article//h1[@class="headline"]', selector_type='xpath', selector_candidates=['//main//h1[@class="headline"]'], found=True)
    ]
    d = DiscoveryResult(status='READY', host='example.com', url='https://example.com/a/1', method='requests_bs4', source='static', template={'title':''}, fields=fields, proposed_json={})
    files = generate('xpath', d, out_dir=str(tmp_path))
    mod = load_module(Path(files[0]))
    soup = BeautifulSoup('<main><article><h1 class="headline">XPath title</h1></article></main>', 'html.parser')
    data, trace = mod.extract_with_trace(soup)
    assert data['title'] == 'XPath title'
    assert trace[0]['result'] == 'exact'


def test_jsonld_object_and_array_are_preserved(tmp_path):
    fields = [
        FieldPlan(name='metrics', path='metrics', required=True, value_type='array', selector='interactionStatistic', selector_type='jsonld', found=True),
        FieldPlan(name='publisher', path='publisher', required=True, value_type='object', selector='publisher', selector_type='jsonld', found=True),
    ]
    d = DiscoveryResult(status='READY', host='example.com', url='https://example.com/a/1', method='requests_bs4', source='static', template={'metrics':[],'publisher':{}}, fields=fields, proposed_json={})
    files = generate('typed-jsonld', d, out_dir=str(tmp_path))
    mod = load_module(Path(files[0]))
    soup = BeautifulSoup('''<script type="application/ld+json">{"interactionStatistic":[{"userInteractionCount":12},{"userInteractionCount":4}],"publisher":{"name":"Pub","@type":"Organization"}}</script>''', 'html.parser')
    data, _ = mod.extract_with_trace(soup)
    assert data['metrics'][0]['userInteractionCount'] == 12
    assert data['publisher']['name'] == 'Pub'
