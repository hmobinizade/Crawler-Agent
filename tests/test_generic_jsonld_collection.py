from app.generic_runtime import ExtractionEngine


class FakeNode:
    def __init__(self, text='', attrs=None):
        self.text=text
        self.attrs=attrs or {}
    def inner_text(self, timeout=None): return self.text
    def get_attribute(self, name, timeout=None): return self.attrs.get(name)
    def count(self): return 1


class FakeScripts:
    def __init__(self, payload): self.payload=payload
    def count(self): return 1
    def nth(self, index): return FakeNode(self.payload)


class FakePage:
    def __init__(self, payload): self.payload=payload
    def locator(self, selector):
        if selector == 'script[type="application/ld+json"]':
            import json
            return FakeScripts(json.dumps(self.payload))
        raise AssertionError(f'unexpected selector: {selector}')


def test_jsonld_collection_candidate_path_fallback():
    payload={'related':[{'url':'https://x/a','title':'A'},{'url':'https://x/b','title':'B'}]}
    structure={'fields':[{
        'name':'news_urls','path':'news_urls','value_type':'array','required':True,
        'selector':'a.related','selector_type':'css',
        'selector_candidates':["script[type='application/ld+json'] path related"],
        'item_selector':'a.related','item_fields':{'url':'@href','title':'text'}
    }]}
    engine=ExtractionEngine(structure)
    data,_=engine._field(FakePage(payload), structure['fields'][0])
    assert data == [{'url':'https://x/a','title':'A'},{'url':'https://x/b','title':'B'}]
