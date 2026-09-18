from app.generic_runtime import ExtractionEngine
from bs4 import BeautifulSoup


def test_jsonld_collection_candidate_path_fallback():
    html='''<html><body><script type="application/ld+json">{"related":[{"url":"https://x/a","title":"A"},{"url":"https://x/b","title":"B"}]}</script></body></html>'''
    structure={'fields':[{'name':'news_urls','path':'news_urls','value_type':'array','required':True,'selector':'a.related','selector_type':'css','selector_candidates':["script[type='application/ld+json'] path related"],'item_selector':'a.related','item_fields':{'url':'@href','title':'text'}}]}
    engine=ExtractionEngine(structure,'requests_bs4')
    data,trace=engine.extract_static(html)
    assert data['news_urls']==[{'url':'https://x/a','title':'A'},{'url':'https://x/b','title':'B'}]
