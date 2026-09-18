from pathlib import Path

from app.codegen import generate
from app.models import DiscoveryResult, FieldPlan


def sample_discovery():
    fields=[
        FieldPlan(name='title', path='title', required=True, value_type='string', selector='h1.article-title', selector_type='css', found=True, value='Title'),
        FieldPlan(name='date', path='date', required=True, value_type='string', selector='meta[property="article:published_time"]', selector_type='meta', attribute='content', found=True, value='2026-09-17'),
        FieldPlan(name='body', path='body', required=True, value_type='string', selector='article.article-body', selector_type='css', found=True, value='Body'),
    ]
    return DiscoveryResult(status='READY', host='example.com', url='https://example.com/article/1', method='playwright', source='playwright', template={'title':'','date':'','body':''}, fields=fields, proposed_json={})


def test_primary_codegen_does_not_call_llm(tmp_path):
    d=sample_discovery()
    files=generate('deterministic', d, out_dir=str(tmp_path))
    assert Path(files[0]).exists()
    src=Path(files[0]).read_text(encoding='utf-8')
    assert 'OpenAI' not in src
    assert 'chat/completions' not in src
    assert 'playwright' in src.lower()
    assert 'BeautifulSoup' not in src


def test_deterministic_codegen_preserves_authoritative_fields(tmp_path):
    d=sample_discovery()
    files=generate('authoritative', d, out_dir=str(tmp_path))
    code=Path(files[0]).read_text(encoding='utf-8')
    for token in ['h1.article-title', 'meta[property="article:published_time"]', 'article.article-body']:
        assert token in code


def test_validator_accepts_playwright_output(tmp_path):
    from app.codegen_agent import CrawlerCodeAgent
    d=sample_discovery()
    files=generate('validate', d, out_dir=str(tmp_path))
    code=Path(files[0]).read_text(encoding='utf-8')
    validation=CrawlerCodeAgent.validate(code, d.model_dump(mode='json'), None)
    assert validation['ok']


def test_llm_is_only_used_for_repair(monkeypatch):
    from app.codegen_agent import CrawlerCodeAgent
    agent=CrawlerCodeAgent()
    called={'value':False}
    class FakeLLM:
        async def chat_json(self, system, user, max_tokens=None):
            called['value']=True
            raise AssertionError('LLM should only be called explicitly from repair()')
    agent.llm=FakeLLM()
    d=sample_discovery()
    validation=agent.validate('print(1)', d.model_dump(mode='json'), None)
    assert not validation['ok']
    assert not called['value']
