from pathlib import Path
import importlib.util

from app.codegen import generate
from app.models import DiscoveryResult, FieldPlan
from bs4 import BeautifulSoup


def load_module(path: Path):
    spec=importlib.util.spec_from_file_location('generated_crawler', path)
    mod=importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def discovery(tmp_path):
    fields=[
        FieldPlan(name='news_code',path='news_code',required=True,value_type='string',selector='meta[property="og:url"]',selector_type='meta',found=True,value='1393482'),
        FieldPlan(name='title',path='title',required=True,value_type='string',selector='h1.Htag',selector_type='css',found=True,value='Example title'),
        FieldPlan(name='body',path='body',required=True,value_type='string',selector='article.article-content',selector_type='css',found=True,value='full'),
        FieldPlan(name='image',path='image',required=True,value_type='string',selector='meta[property="og:image"]',selector_type='meta',found=True,value='https://x/image.jpg'),
        FieldPlan(name='date',path='date',required=True,value_type='string',selector='meta[property="article:published_time"]',selector_type='meta',found=True,value='2026-09-16'),
        FieldPlan(name='author',path='author',required=False,value_type='string',selector='meta[name="author"]',selector_type='meta',found=True,value='Author'),
        FieldPlan(name='view_count',path='view_count',required=False,value_type='string',selector='main.container.night_mode_news',selector_type='css',found=True,value='1234'),
    ]
    return DiscoveryResult(status='READY',host='www.tabnak.ir',url='https://www.tabnak.ir/fa/news/1393482/x',method='requests_bs4',source='static',template={f.path:'' for f in fields},fields=fields,proposed_json={})


def test_tabnak_style_static_generator_extracts_full_body(tmp_path):
    d=discovery(tmp_path)
    files=generate('quality',d,out_dir=str(tmp_path))
    mod=load_module(Path(files[0]))
    html='''
    <html><head>
      <meta property="og:url" content="https://www.tabnak.ir/fa/news/1393482/x">
      <meta property="og:image" content="https://x/image.jpg">
      <meta property="article:published_time" content="2026-09-16T10:00:00">
    </head><body>
      <main class="container night_mode_news">
        <h1 class="Htag">Example title</h1>
        <span>1,234 بازدید</span>
        <article class="article-content">
          <p>First real paragraph of the article.</p>
          <div class="advertisement">تبلیغ مزاحم</div>
          <p>Second real paragraph with substantially more article text.</p>
          <div class="related">مطالب مرتبط</div>
          <p>Third paragraph after the ad and related block.</p>
        </article>
      </main>
    </body></html>
    '''
    soup=BeautifulSoup(html,'html.parser')
    data=mod.extract(soup)
    assert data['news_code']=='1393482'
    assert data['title']=='Example title'
    assert 'First real paragraph' in data['body']
    assert 'Second real paragraph' in data['body']
    assert 'Third paragraph' in data['body']
    assert 'تبلیغ مزاحم' not in data['body']
    assert 'مطالب مرتبط' not in data['body']
    assert data['image']=='https://x/image.jpg'
    assert data['date']=='2026-09-16T10:00:00'
    assert data['view_count']=='1234'


def test_generated_files_compile(tmp_path):
    d=discovery(tmp_path)
    files=generate('compile',d,out_dir=str(tmp_path))
    src=Path(files[0]).read_text(encoding='utf-8')
    compile(src,files[0],'exec')


def test_collection_codegen_extracts_all_items(tmp_path):
    fields=[FieldPlan(name='comments',path='comments',required=True,value_type='array',selector='.comments > article.comment',selector_type='css',found=True,item_selector='.comments > article.comment',item_fields={
        'author': {'selector': '.author'},
        'text': {'selector': '.text'},
        'likes': {'selector': '.likes'},
    },collection_count=2)]
    d=DiscoveryResult(status='READY',host='example.com',url='https://example.com/article/1',method='requests_bs4',source='static',template={'comments':[]},fields=fields,proposed_json={})
    files=generate('collection',d,out_dir=str(tmp_path))
    mod=load_module(Path(files[0]))
    html='''<div class="comments"><article class="comment"><span class="author">A</span><p class="text">First comment</p><span class="likes">2</span></article><article class="comment"><span class="author">B</span><p class="text">Second comment</p><span class="likes">3</span></article></div>'''
    data=mod.extract(BeautifulSoup(html,'html.parser'))
    assert data['comments']==[{'author':'A','text':'First comment','likes':'2'},{'author':'B','text':'Second comment','likes':'3'}]


def test_codegen_honors_analyzed_selector_before_fallbacks(tmp_path):
    fields=[
        FieldPlan(name='date',path='date',required=True,value_type='string',selector='div.story-meta > time.publish-date',selector_type='css',found=True,value='2026-09-16'),
        FieldPlan(name='body',path='body',required=True,value_type='string',selector='section.real-story-body',selector_type='css',found=True,value='The full article body'),
    ]
    d=DiscoveryResult(status='READY',host='example.com',url='https://example.com/article/1',method='requests_bs4',source='static',template={'date':'','body':''},fields=fields,proposed_json={})
    files=generate('exact-rule',d,out_dir=str(tmp_path))
    mod=load_module(Path(files[0]))
    html='''<main><div class="wrong-date-body">This must never become the date.</div><div class="story-meta"><time class="publish-date">2026-09-16</time></div><section class="real-story-body"><p>Paragraph one of the complete article.</p><p>Paragraph two of the complete article.</p></section></main>'''
    data=mod.extract(BeautifulSoup(html,'html.parser'))
    assert data['date']=='2026-09-16'
    assert 'Paragraph one' in data['body'] and 'Paragraph two' in data['body']
    assert 'wrong-date-body' not in data['date']


def test_farsnews_analysis_contract_is_preserved_in_codegen(tmp_path):
    fields=[
        FieldPlan(name='news_code',path='news_code',required=True,value_type='string',selector='mainEntityOfPage.@id',selector_type='jsonld',found=True,value='1789494619808281205'),
        FieldPlan(name='title',path='title',required=True,value_type='string',selector='h1.n-3etg02.text-selection-lines',selector_type='css',found=True,value='Title'),
        FieldPlan(name='body',path='body',required=True,value_type='string',selector='div.px-post-padding-x.pb-2:nth-of-type(1)',selector_type='css',found=True,value='Body'),
        FieldPlan(name='topic',path='topic',required=True,value_type='string',selector='فرهنگ حماسه و مقاومت',selector_type='text',scope_selector='div.px-post-padding-x.pb-2:nth-of-type(1)',found=True,value='فرهنگ حماسه و مقاومت'),
        FieldPlan(name='categories',path='categories',required=True,value_type='array',selector='فرهنگ حماسه و مقاومت',selector_type='text',scope_selector='div.px-post-padding-x.pb-2:nth-of-type(1)',found=True,value=['فرهنگ حماسه و مقاومت'],item_selector='فرهنگ حماسه و مقاومت',collection_count=1),
        FieldPlan(name='media',path='media',required=True,value_type='array',selector='image',selector_type='jsonld',found=True,value=['https://x/image.jpg'],item_selector='image[]',collection_count=1),
        FieldPlan(name='news_urls',path='news_urls',required=True,value_type='array',selector='mainEntityOfPage.@id',selector_type='jsonld',found=True,value=['https://farsnews.ir/a/1789494619808281205'],item_selector='mainEntityOfPage.@id',collection_count=1),
        FieldPlan(name='date',path='date',required=True,value_type='string',selector='datePublished',selector_type='jsonld',found=True,value='2026-09-15T17:50:19.000Z'),
        FieldPlan(name='author',path='author',required=True,value_type='string',selector='author.name',selector_type='jsonld',found=True,value='فاطمه ملکی'),
        FieldPlan(name='all_metrics',path='all_metrics',required=True,value_type='array',selector='interactionStatistic',selector_type='jsonld',found=True,value=[{'interactionType':'https://schema.org/ViewAction','userInteractionCount':66702}],item_selector='interactionStatistic[]',collection_count=5),
    ]
    d=DiscoveryResult(status='READY',host='farsnews.ir',url='https://farsnews.ir/a/1789494619808281205',method='requests_bs4',source='static',template={f.path:'' for f in fields},fields=fields,proposed_json={})
    files=generate('fars-contract',d,out_dir=str(tmp_path))
    mod=load_module(Path(files[0]))
    html='''<html><head>
      <script type="application/ld+json">{
        "mainEntityOfPage":{"@id":"https://farsnews.ir/a/1789494619808281205"},
        "datePublished":"2026-09-15T17:50:19.000Z",
        "author":{"name":"فاطمه ملکی"},
        "image":["https://x/image.jpg"],
        "interactionStatistic":[
          {"@type":"InteractionCounter","interactionType":"https://schema.org/ViewAction","userInteractionCount":66702},
          {"@type":"InteractionCounter","interactionType":"https://schema.org/ReplyAction","userInteractionCount":8}
        ]
      }</script>
    </head><body>
      <main><article>
        <h1 class="n-3etg02 text-selection-lines">Title</h1>
        <div class="px-post-padding-x pb-2">
          <time>2026-09-15</time><span>فرهنگ حماسه و مقاومت</span>
          <p>Paragraph one from the article body, with enough text to be preserved.</p>
          <div class="advertisement">تبلیغ</div>
          <p>Paragraph two from the article body, with enough text to be preserved.</p>
        </div>
      </article></main>
    </body></html>'''
    data=mod.extract(BeautifulSoup(html,'html.parser'))
    assert data['news_code']=='1789494619808281205'
    assert data['title']=='Title'
    assert 'Paragraph one' in data['body'] and 'Paragraph two' in data['body']
    assert data['topic']=='فرهنگ حماسه و مقاومت'
    assert data['categories']==['فرهنگ حماسه و مقاومت']
    assert data['media']==['https://x/image.jpg']
    assert data['news_urls']==['https://farsnews.ir/a/1789494619808281205']
    assert data['date']=='2026-09-15T17:50:19.000Z'
    assert data['author']=='فاطمه ملکی'
    assert isinstance(data['all_metrics'], list) and data['all_metrics'][0]['userInteractionCount']==66702
