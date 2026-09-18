import pytest

import standalone.playwright_structure_crawler as runtime
from app.generic_runtime import ExtractionEngine, run_structure


def test_generic_runtime_is_playwright_only(monkeypatch, tmp_path):
    called = {}
    def fake(structure, **kwargs):
        called.update(kwargs)
        return {'method':'playwright','record_count':1,'success_count':1,'failed_count':0,'records':[],'output_path':str(kwargs['output'])}
    monkeypatch.setattr(runtime, 'crawl_playwright', fake)
    structure={'execution':{'mode':'generic'},'fields':[]}
    result=run_structure(structure,start_url='https://example.com/a/1',output=tmp_path/'out.jsonl')
    assert result['method']=='playwright'
    assert called['start_url']=='https://example.com/a/1'


def test_generic_runtime_rejects_custom_mode():
    with pytest.raises(ValueError):
        run_structure({'execution':{'mode':'custom'},'fields':[]}, start_url='https://example.com/a/1', output='/tmp/x.jsonl')


def test_extraction_engine_contract_shape():
    structure={'fields':[{'name':'title','path':'title','selector':'h1','selector_type':'css','selector_candidates':['h1','.title']}]}
    engine=ExtractionEngine(structure)
    assert engine.fields[0]['selector']=='h1'
    assert engine.fields[0]['selector_candidates']==['h1','.title']
