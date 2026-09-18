from pathlib import Path
import importlib.util

from app.codegen import generate
from app.codegen_agent import CrawlerCodeAgent
from app.models import DiscoveryResult, FieldPlan
from bs4 import BeautifulSoup


def sample_discovery(method='requests_bs4'):
    fields=[
        FieldPlan(name='title', path='title', required=True, value_type='string', selector='h1.article-title', selector_type='css', found=True, value='Title'),
        FieldPlan(name='date', path='date', required=True, value_type='string', selector='meta[property="article:published_time"]', selector_type='meta', attribute='content', found=True, value='2026-09-17'),
        FieldPlan(name='body', path='body', required=True, value_type='string', selector='article.article-body', selector_type='css', found=True, value='Body'),
    ]
    return DiscoveryResult(status='READY', host='example.com', url='https://example.com/article/1', method=method, source='static', template={'title':'','date':'','body':''}, fields=fields, proposed_json={})


def load_module(path: Path):
    spec=importlib.util.spec_from_file_location('generated_crawler_v16', path)
    mod=importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_primary_codegen_does_not_call_llm(tmp_path):
    d=sample_discovery()
    files=generate('deterministic', d, out_dir=str(tmp_path))
    assert Path(files[0]).exists()
    src=Path(files[0]).read_text(encoding='utf-8')
    assert 'OpenAI' not in src
    assert 'chat/completions' not in src


def test_deterministic_codegen_extracts_authoritative_fields(tmp_path):
    d=sample_discovery()
    files=generate('authoritative', d, out_dir=str(tmp_path))
    mod=load_module(Path(files[0]))
    html='''<html><head><meta property="article:published_time" content="2026-09-17"></head><body>
    <div class="wrong-date">This is not the date</div>
    <h1 class="article-title">Exact title</h1>
    <article class="article-body"><p>First complete paragraph of the article.</p><p>Second complete paragraph of the article.</p></article>
    </body></html>'''
    data=mod.extract(BeautifulSoup(html,'html.parser'))
    assert data['title']=='Exact title'
    assert data['date']=='2026-09-17'
    assert 'First complete paragraph' in data['body']
    assert 'Second complete paragraph' in data['body']


def test_validator_accepts_deterministic_output(tmp_path):
    d=sample_discovery()
    files=generate('validate', d, out_dir=str(tmp_path))
    code=Path(files[0]).read_text(encoding='utf-8')
    html='''<h1 class=\"article-title\">Title</h1><meta property=\"article:published_time\" content=\"2026-09-17\"><article class=\"article-body\"><p>Some sufficiently long body text here.</p></article>'''
    validation=CrawlerCodeAgent.validate(code, d.model_dump(mode='json'), html)
    assert validation['ok']


def test_llm_is_only_used_for_repair(monkeypatch):
    agent=CrawlerCodeAgent()
    called={'value':False}
    class FakeLLM:
        async def chat_json(self, system, user, max_tokens=None):
            called['value']=True
            raise AssertionError('LLM should only be called explicitly from repair()')
    agent.llm=FakeLLM()
    d=sample_discovery()
    code='print(1)'
    validation=agent.validate(code, d.model_dump(mode='json'), None)
    assert not validation['ok']
    assert not called['value']
