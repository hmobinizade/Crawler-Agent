from app.codegen import generate
from app.models import DiscoveryResult, FieldPlan


def test_codegen_layout(tmp_path):
    d = DiscoveryResult(
        status='READY', host='example.com', url='https://example.com/article/1',
        template={'title':'', 'date':''}, proposed_json={'title':'A','date':'2026-01-01'},
        fields=[
            FieldPlan(name='title', path='title', selector='article h1.title', selector_type='css', found=True, value='A'),
            FieldPlan(name='date', path='date', selector="meta[property='article:published_time']", selector_type='meta', attribute='content', found=True, value='2026-01-01'),
        ],
    )
    paths = generate('test123', d, out_dir=str(tmp_path))
    assert paths[0].endswith('example.com/job_test123/09_crawler.py')
    assert paths[1].endswith('example.com/job_test123/crawler.schema.json')
