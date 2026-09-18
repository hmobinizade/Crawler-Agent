from pathlib import Path
import ast

from app.codegen import generate
from app.models import DiscoveryResult, FieldPlan


def test_selector_candidates_are_serialized_without_reinterpretation(tmp_path):
    fields = [
        FieldPlan(
            name='date', path='date', required=True, selector='time.publish-date',
            selector_type='css', selector_candidates=['time.publish-date', 'meta[property="article:published_time"]'],
            scope_selector='div.meta', found=True,
        ),
        FieldPlan(
            name='media', path='media', required=True, value_type='array',
            selector='image', selector_type='jsonld', selector_candidates=['image', 'thumbnailUrl'], found=True,
        ),
    ]
    d = DiscoveryResult(
        status='READY', host='example.com', url='https://example.com/article/1',
        method='playwright', source='playwright', template={'date':'','media':[]},
        fields=fields, proposed_json={},
    )
    paths = generate('exact-contract', d, out_dir=str(tmp_path))
    code = Path(paths[0]).read_text(encoding='utf-8')
    ast.parse(code)
    for token in ['time.publish-date', 'meta[property="article:published_time"]', 'div.meta', 'image', 'thumbnailUrl']:
        assert token in code
