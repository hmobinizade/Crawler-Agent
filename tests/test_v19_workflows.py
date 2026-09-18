from pathlib import Path
import importlib.util

from app.artifacts import ArtifactStore
from app.crawler_runner import run_crawler
from app.codegen import generate
from app.models import DiscoveryResult, FieldPlan


def load_module(path):
    spec = importlib.util.spec_from_file_location('generated_v19_crawler', path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def test_generated_crawler_supports_runtime_overrides(tmp_path):
    discovery = DiscoveryResult(
        status='READY', host='example.com', url='https://example.com/a/123', method='requests_bs4', source='static',
        template={'title': ''}, proposed_json={'title': 'Hello'},
        fields=[FieldPlan(name='title', path='title', selector='h1.title', selector_type='css', found=True, value='Hello')]
    )
    paths = generate('runtime_override', discovery, out_dir=str(tmp_path))
    code = Path(paths[0]).read_text(encoding='utf-8')
    assert 'CRAWLER_START_URL' in code
    assert 'CRAWLER_OUT' in code
    assert 'CRAWLER_MAX_PAGES' in code


def test_artifact_jobs_and_analysis_response(tmp_path):
    store = ArtifactStore(str(tmp_path / 'generated'))
    discovery = DiscoveryResult(
        status='READY', host='example.com', url='https://example.com/a/1', method='requests_bs4', source='static',
        template={'title': ''}, proposed_json={'title':'x'},
        fields=[FieldPlan(name='title', path='title', found=True, value='x')]
    )
    store.save_discovery(job_id='abc123', url=discovery.url, template=discovery.template, discovery=discovery,
                         source_html='<html></html>', discovery_prompt='PROMPT',
                         analysis_response={'mode':'static','ok':True})
    jobs = store.job_entries()
    assert jobs[0]['job_id'] == 'abc123'
    assert jobs[0]['domain'] == 'example.com'
    assert (store.root/'example.com'/'job_abc123'/'06_analysis_response.json').exists()


def test_generated_crawler_runs_end_to_end(tmp_path):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    pages = {
        '/news/1': '<html><body><h1 class="title">One</h1><a href="/news/2">Next</a></body></html>',
        '/news/2': '<html><body><h1 class="title">Two</h1></body></html>',
    }
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = pages.get(self.path, '<html><body>404</body></html>').encode()
            self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        base = f'http://127.0.0.1:{server.server_port}'
        url = base + '/news/1'
        discovery = DiscoveryResult(
            status='READY', host='127.0.0.1', url=url, method='requests_bs4', source='static',
            template={'title': ''}, proposed_json={'title': 'One'},
            fields=[FieldPlan(name='title', path='title', selector='h1.title', selector_type='css', found=True, value='One')]
        )
        paths = generate('e2e', discovery, out_dir=str(tmp_path / 'generated'))
        output = tmp_path / 'run.jsonl'
        run = run_crawler(paths[0], url=url, output=output, max_pages=2, timeout_seconds=30, headless=True)
        assert run['record_count'] == 2
        assert run['success_count'] == 2
        assert output.exists()
    finally:
        server.shutdown(); thread.join(timeout=2)
