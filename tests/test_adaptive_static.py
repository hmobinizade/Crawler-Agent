from app.static_extractor import field_from_static
from app.adaptive import flatten_template
from bs4 import BeautifulSoup


def test_static_news_fields():
    html='''<html><head><meta property="og:image" content="https://x/img.jpg"><meta property="article:published_time" content="2026-09-10"></head><body><article class="news-detail"><h1 class="article-title">Title</h1><div class="article-body"><p>First long paragraph with enough content to count as article text and not be treated as noise.</p><p>Second long paragraph with enough content to count as article text.</p><div class="advertisement">تبلیغ</div></div></article></body></html>'''
    soup=BeautifulSoup(html,'html.parser')
    contract=flatten_template({'title':'','body':'','image':'','date':''})
    result={f['path']:field_from_static(soup,f)[1] for f in contract}
    assert result['title']=='Title'
    assert result['image']=='https://x/img.jpg'
    assert result['date']=='2026-09-10'
    assert 'First long paragraph' in result['body']
    assert 'Second long paragraph' in result['body']
    assert 'تبلیغ' not in result['body']
