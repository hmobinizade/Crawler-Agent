from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app.generic_runtime import run_structure

PAGES = {
    "/article/1": """<html><head><meta property='og:image' content='https://img/1.jpg'></head><body>
      <article class='article'><h1>Title 1</h1><time class='date'>2026-09-17</time>
      <p>First paragraph of article one.</p><p>Second paragraph of article one.</p>
      <a class='next-article' href='/article/2'>Next</a>
      <a class='related' href='/article/2'>Related article</a></article>
    </body></html>""",
    "/article/2": """<html><head><meta property='og:image' content='https://img/2.jpg'></head><body>
      <article class='article'><h1>Title 2</h1><time class='date'>2026-09-18</time>
      <p>First paragraph of article two.</p><p>Second paragraph of article two.</p>
      <a class='next-article' href='/article/1'>Back</a></article>
    </body></html>""",
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGES.get(self.path)
        if body is None:
            self.send_response(404); self.end_headers(); return
        raw = body.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers(); self.wfile.write(raw)
    def log_message(self, *args):
        return


def test_generic_runtime_reuses_one_structure_for_multiple_pages(tmp_path):
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        root = f'http://127.0.0.1:{server.server_port}/article/1'
        structure = {
            'url': root,
            'host': f'127.0.0.1:{server.server_port}',
            'method': 'requests_bs4',
            'crawl': {'page_pattern': r'^/article/\d+$', 'link_selector': 'a.next-article', 'delay_seconds': 0},
            'fields': [
                {'name':'title','path':'title','required':True,'value_type':'string','selector':'h1','selector_type':'css'},
                {'name':'date','path':'date','required':True,'value_type':'string','selector':'time.date','selector_type':'css'},
                {'name':'image','path':'image','required':True,'value_type':'string','selector':'meta[property="og:image"]','selector_type':'meta'},
                {'name':'body','path':'body','required':True,'value_type':'string','selector':'article.article','selector_type':'css','extraction_hint':'full article body'},
            ],
        }
        out = tmp_path / 'results.jsonl'
        result = run_structure(structure, start_url=root, output=out, max_pages=5)
        assert result['record_count'] == 2
        assert result['success_count'] == 2
        rows = [json.loads(x) for x in out.read_text(encoding='utf-8').splitlines()]
        assert {r['data']['title'] for r in rows} == {'Title 1', 'Title 2'}
        assert all('First paragraph' in r['data']['body'] and 'Second paragraph' in r['data']['body'] for r in rows)
    finally:
        server.shutdown(); server.server_close()
