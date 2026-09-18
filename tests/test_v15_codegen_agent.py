import json
from pathlib import Path

from app.codegen_agent import CrawlerCodeAgent
from app.models import DiscoveryResult, FieldPlan


def sample_discovery():
    return DiscoveryResult(
        status='READY',
        host='example.com',
        url='https://example.com/article/1',
        method='requests_bs4',
        source='static',
        template={'title':'','date':'','body':''},
        fields=[
            FieldPlan(name='title', path='title', required=True, selector='h1.article-title', selector_type='css', value_type='string', found=True, value='Title'),
            FieldPlan(name='date', path='date', required=True, selector='meta[property="article:published_time"]', selector_type='meta', attribute='content', value_type='string', found=True, value='2026-09-17'),
            FieldPlan(name='body', path='body', required=True, selector='article.article-body', selector_type='css', value_type='string', found=True, value='Body'),
        ],
    )


def test_codegen_prompt_contains_authoritative_structure_and_html():
    agent = CrawlerCodeAgent()
    d = sample_discovery()
    prompt = agent.build_prompt(d.model_dump(mode='json'), '<html><h1>Title</h1></html>')
    assert 'EXTRACTION STRUCTURE' in prompt
    assert 'SOURCE PAGE HTML' in prompt
    assert 'h1.article-title' in prompt


def test_codegen_source_validator_rejects_llm_runtime():
    agent = CrawlerCodeAgent()
    d = sample_discovery().model_dump(mode='json')
    bad = 'import openai\nROOT="x"\nFIELDS=[]\nREQUIRED=[]\n'
    result = agent.validate_source(bad, d)
    assert not result['ok']
    assert any('LLM' in e for e in result['errors'])


def test_artifact_contract_files_exist(tmp_path):
    from app.artifacts import ArtifactStore
    store = ArtifactStore(str(tmp_path))
    d = sample_discovery()
    files = store.save_discovery(job_id='abc123', url=d.url, template=d.template, discovery=d, source_html='<html/>', discovery_prompt='DISCOVERY', compact_evidence='EVIDENCE')
    assert any(Path(x).name == '02_extraction_structure.json' for x in files)
    assert any(Path(x).name == '06_source_page.html' for x in files)
    saved = store.read_source_html(d.url, 'abc123')
    assert saved == '<html/>'


def test_codegen_agent_generates_and_smoke_validates_requests_code(monkeypatch):
    agent = CrawlerCodeAgent()
    discovery = sample_discovery()
    generated = '''
import re, json
from bs4 import BeautifulSoup
ROOT = "https://example.com/article/1"
START_URL = ROOT
FIELDS = [{"path":"title"},{"path":"date"},{"path":"body"}]
REQUIRED = ["title","date","body"]
def clean_text(v): return re.sub(r"\\s+", " ", str(v or "")).strip() or None
def set_path(o,p,v): o[p]=v
def missing_required(data): return [p for p in REQUIRED if not data.get(p)]
def extraction_ok(data): return not missing_required(data)
def extract(soup):
    return {"title": soup.select_one("h1.article-title").get_text(" ",strip=True), "date": soup.select_one("meta[property=\\\"article:published_time\\\"]").get("content"), "body": soup.select_one("article.article-body").get_text(" ",strip=True)}
if __name__ == "__main__":
    import argparse; argparse.ArgumentParser().add_argument("--once")
'''

    class FakeLLM:
        async def chat_json(self, system, user, max_tokens=None):
            return {
                'filename': 'crawler_test.py',
                'code': generated,
                'runtime': 'requests_bs4',
                'notes': [],
                'required_packages': ['beautifulsoup4'],
                'contract_preserved': True,
            }

    agent.llm = FakeLLM()
    html = '<h1 class="article-title">Title</h1><meta property="article:published_time" content="2026-09-17"><article class="article-body">Full body text</article>'

    import asyncio
    result = asyncio.run(agent.generate(discovery, html=html))
    assert result['validation']['ok'] is True
    assert result['runtime'] == 'requests_bs4'
