from __future__ import annotations

import json
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .adaptive import AdaptiveAgent
from .artifacts import artifact_store
from .codegen import generate as deterministic_generate
from .codegen_agent import CrawlerCodeAgent, CodeGenerationError
from .crawler_runner import CrawlerRunError, run_crawler
from .generic_runtime import run_structure
from .structure_registry import StructureRegistry
from .host_registry import HostAnalysisRegistry
from .local_registry import LocalCrawlerRegistry
from .models import CodegenRequest, ConfirmRequest, CrawlRequest, DiscoverRequest, Job, DiscoveryResult
from .session_manager import BrowserSessionManager
from .store import store

app = FastAPI(title='Agentic Crawler Studio', version='2.0.0')
app.mount('/static', StaticFiles(directory='frontend/static'), name='static')

host_registry = HostAnalysisRegistry()
local_registry = LocalCrawlerRegistry()
browser_sessions = BrowserSessionManager()
code_agent = CrawlerCodeAgent()
structure_registry = StructureRegistry()


@app.get('/')
async def discover_page():
    return FileResponse('frontend/static/discover.html')


@app.get('/codegen')
async def codegen_page():
    return FileResponse('frontend/static/codegen.html')


@app.get('/crawler')
async def crawler_page():
    return FileResponse('frontend/static/crawler.html')


@app.get('/generated')
async def generated_page():
    return FileResponse('frontend/static/generated.html')


@app.get('/files')
async def files_page():
    return FileResponse('frontend/static/generated.html')


@app.get('/health')
async def health():
    return {'ok': True, 'browser_sessions': len(browser_sessions._sessions)}


@app.get('/artifacts/tree')
async def artifacts_tree():
    return {'root': 'generated', 'tree': artifact_store.tree()}


@app.get('/artifacts/jobs')
async def artifacts_jobs():
    return {'jobs': artifact_store.job_entries()}


@app.get('/artifacts/jobs/{job_id}')
async def artifact_job(job_id: str):
    item = artifact_store.job_entry(job_id)
    if not item:
        raise HTTPException(status_code=404, detail='Unknown artifact job')
    structure = artifact_store.read_structure(item['url'], job_id) if item.get('url') else None
    return {'job': item, 'structure': structure}


@app.get('/artifacts/preview')
async def artifacts_preview(path: str):
    try:
        target = artifact_store.safe_resolve(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail='Artifact file not found')
    if target.stat().st_size > 1_500_000:
        raise HTTPException(status_code=413, detail='File is too large for preview; download it instead')
    try:
        text = target.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        raise HTTPException(status_code=415, detail='Binary file cannot be previewed')
    return {'path': path.replace('\\', '/'), 'name': target.name, 'size': target.stat().st_size, 'content': text}


@app.get('/artifacts/download')
async def artifacts_download(path: str):
    try:
        target = artifact_store.safe_resolve(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail='Artifact file not found')
    return FileResponse(str(target), filename=target.name, media_type='application/octet-stream')


@app.get('/artifacts/zip')
async def artifacts_zip(path: str, background_tasks: BackgroundTasks):
    try:
        target = artifact_store.safe_resolve(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=404, detail='Artifact folder not found')
    tmp = tempfile.NamedTemporaryFile(prefix='crawler_artifacts_', suffix='.zip', delete=False)
    tmp.close()
    zip_path = Path(tmp.name)
    try:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for file in target.rglob('*'):
                if file.is_file() and '.browser-profile' not in file.parts:
                    zf.write(file, file.relative_to(target))
    except Exception:
        zip_path.unlink(missing_ok=True)
        raise
    background_tasks.add_task(zip_path.unlink, missing_ok=True)
    return FileResponse(str(zip_path), filename=f'{target.name}.zip', media_type='application/zip', background=background_tasks)


@app.get('/structures')
async def structures_list():
    return {'structures': structure_registry.list()}


@app.get('/structures/{structure_id}')
async def structure_get(structure_id: str):
    value = structure_registry.get(structure_id)
    if not value:
        raise HTTPException(status_code=404, detail='Unknown structure')
    return value


@app.on_event('shutdown')
async def shutdown_event():
    await browser_sessions.close_all()


@app.post('/discover', response_model=Job)
async def discover(req: DiscoverRequest):
    agent = AdaptiveAgent(registry=host_registry, local_registry=local_registry)
    try:
        # Discovery is a complete workflow. For Playwright pages the Python side owns
        # navigation/scroll/evidence; the LLM receives compact evidence only.
        result = await agent.inspect(str(req.url), req.template, allow_playwright=True)
        job = Job(id=uuid4().hex[:12], discovery=result)

        artifacts = {}
        if getattr(agent, 'browser_agent', None) is not None:
            artifacts = getattr(agent.browser_agent, 'last_artifacts', {}) or {}

        artifact_store.save_discovery(
            job_id=job.id,
            url=result.url,
            template=req.template,
            discovery=result,
            source_html=artifacts.get('source_html') or getattr(agent, 'last_static_html', None),
            routing_prompt=getattr(agent, 'last_routing_prompt', None),
            discovery_prompt=artifacts.get('discovery_prompt'),
            compact_evidence=artifacts.get('compact_evidence'),
            analysis_response=artifacts.get('analysis_response') or {'mode': result.source, 'status': result.status},
        )
        structure_registry.save(result.model_dump(mode='json'), structure_id=job.id)
        host_registry.mark(result.url, job.id, result.method)
        store.put(job)
        return job
    except RuntimeError as exc:
        if str(exc).startswith('HOST_ALREADY_ANALYZED:'):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# Kept for backward compatibility with v10/v11 clients. New UI does not use this flow.
@app.post('/analyze-current/{job_id}', response_model=Job)
async def analyze_current(job_id: str):
    try:
        job = store.get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail='Unknown job') from exc
    if not job.browser_session_id or not job.needs_browser_analysis:
        raise HTTPException(status_code=409, detail='No pending browser session')
    try:
        result, artifacts = await browser_sessions.analyze(job.browser_session_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f'Interactive browser analysis failed: {exc}') from exc
    job.discovery = result
    job.needs_browser_analysis = False
    job.browser_session_id = None
    artifact_store.save_discovery(
        job_id=job.id,
        url=result.url,
        template=job.discovery.template,
        discovery=result,
        source_html=artifacts.get('source_html'),
        discovery_prompt=artifacts.get('discovery_prompt'),
        compact_evidence=artifacts.get('compact_evidence'),
        analysis_response=artifacts.get('analysis_response'),
    )
    await browser_sessions.close(job.browser_session_id) if job.browser_session_id else None
    store.put(job)
    return job


@app.get('/jobs/{job_id}', response_model=Job)
async def get_job(job_id: str):
    try:
        return store.get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail='Unknown job') from exc


async def _load_discovery_for_codegen(req: CodegenRequest) -> tuple[DiscoveryResult, str | None, str]:
    if req.source_job_id:
        info = artifact_store.job_entry(req.source_job_id)
        if not info:
            raise HTTPException(status_code=404, detail='Unknown source job')
        structure = artifact_store.read_structure(info['url'], req.source_job_id)
        if not structure:
            raise HTTPException(status_code=404, detail='Source job has no extraction structure')
        url = str(req.url or structure.get('url') or info.get('url') or '')
        if not url:
            raise HTTPException(status_code=422, detail='Source job has no URL')
        html = req.html or artifact_store.read_source_html(url, req.source_job_id)
    else:
        structure = req.structure
        url = str(req.url or (structure or {}).get('url') or '')
        if not structure or not url:
            raise HTTPException(status_code=422, detail='Provide source_job_id or structure + url')
        html = req.html

    structure = dict(structure)
    structure.setdefault('status', 'READY')
    structure.setdefault('host', urlparse(url).netloc.lower())
    structure.setdefault('method', 'requests_bs4')
    structure.setdefault('source', 'none')
    structure.setdefault('template', {})
    try:
        discovery = DiscoveryResult.model_validate(structure)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f'Invalid extraction structure: {exc}') from exc
    return discovery, html, url


async def _ensure_codegen(discovery: DiscoveryResult, html: str | None, job_id: str) -> tuple[str, list[str], dict]:
    generated_files = deterministic_generate(job_id, discovery, out_dir='generated')
    code_path = next((p for p in generated_files if p.endswith('.py')), None)
    if not code_path:
        raise CodeGenerationError('Deterministic generator did not produce a crawler')
    code = Path(code_path).read_text(encoding='utf-8')
    structure_dict = discovery.model_dump(mode='json')
    validation = code_agent.validate(code, structure_dict, html)
    if not validation['ok']:
        repair_info = await code_agent.repair(structure_dict, code, validation, html)
        code = repair_info['code']
        Path(code_path).write_text(code, encoding='utf-8')
        validation = repair_info['validation']
        repair_info.setdefault('required_packages', [])
        return code, generated_files, {'validation': validation, 'repair': repair_info}
    return code, generated_files, {'validation': validation, 'repair': None}


@app.post('/generate-code')
async def generate_code(req: CodegenRequest):
    discovery, html, url = await _load_discovery_for_codegen(req)
    job_id = req.source_job_id or f'code_{uuid4().hex[:10]}'
    try:
        code, generated_files, info = await _ensure_codegen(discovery, html, job_id)
    except CodeGenerationError as exc:
        raise HTTPException(status_code=502, detail=f'Code generation failed: {exc}') from exc

    repair = info['repair']
    files = artifact_store.save_discovery(
        job_id=job_id,
        url=url,
        template=discovery.template,
        discovery=discovery,
        source_html=html,
    )
    files += artifact_store.save_codegen(
        job_id=job_id,
        url=url,
        code=code,
        codegen_prompt=(repair['repair_prompt'] if repair else 'Deterministic code generation from ExtractionSpec.'),
        llm_response=(repair['llm_response'] if repair else {'used': False, 'mode': 'deterministic-first'}),
        validation=info['validation'],
    )
    return {
        'job_id': job_id,
        'domain': urlparse(url).netloc.lower(),
        'filename': Path(next(p for p in generated_files if p.endswith('.py'))).name,
        'runtime': discovery.method,
        'notes': (repair['notes'] if repair else ['Deterministic generator used; LLM is repair-only.']),
        'required_packages': (repair.get('required_packages') if repair else (['requests', 'beautifulsoup4'] if discovery.method in {'requests_bs4', 'static'} else ['playwright'])),
        'validation': info['validation'],
        'files': sorted(set(generated_files + files)),
        'code': code,
    }


@app.post('/crawl')
async def crawl(req: CrawlRequest):
    structure = req.structure
    url = str(req.url or '')
    job_id = req.source_job_id

    if req.structure_id:
        structure = structure_registry.get(req.structure_id)
        if not structure:
            raise HTTPException(status_code=404, detail='Unknown structure')
        url = url or str(structure.get('url') or structure.get('start_url') or '')
        job_id = job_id or f"structure_{req.structure_id}"

    if req.source_job_id:
        info = artifact_store.job_entry(req.source_job_id)
        if not info:
            raise HTTPException(status_code=404, detail='Unknown source job')
        structure = artifact_store.read_structure(info['url'], req.source_job_id)
        url = url or info['url']
        if not structure:
            raise HTTPException(status_code=404, detail='Source job has no extraction structure')
        job_id = req.source_job_id

    if not structure or not url:
        raise HTTPException(status_code=422, detail='Provide source_job_id, structure_id, or structure + url')

    # Generic runtime is the default and does not generate a per-job crawler file.
    if req.mode == 'generic':
        run_job_id = job_id or f"crawl_{uuid4().hex[:10]}"
        output_dir = artifact_store.job_dir(url, run_job_id)
        output = output_dir / '11_crawl_results.jsonl'
        try:
            run_info = run_structure(
                structure,
                start_url=url,
                output=output,
                max_pages=req.max_pages,
                timeout_seconds=req.timeout_seconds,
                headless=req.headless,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f'Generic crawler execution failed: {exc}') from exc
        saved = artifact_store.save_crawl(job_id=run_job_id, url=url, run_info=run_info)
        result_rel = str(output.resolve().relative_to(artifact_store.root.resolve())).replace('\\', '/') if output.resolve().is_relative_to(artifact_store.root.resolve()) else str(output)
        return {
            'job_id': run_job_id,
            'structure_id': structure.get('structure_id'),
            'url': url,
            'mode': 'generic',
            'runtime': structure.get('method', 'requests_bs4'),
            'record_count': run_info['record_count'],
            'success_count': run_info['success_count'],
            'failed_count': run_info['failed_count'],
            'records': run_info['records'],
            'files': sorted(set(saved)),
            'result_file': result_rel,
            'result_download': '/artifacts/download?path=' + result_rel,
        }

    # Explicit custom mode: legacy Codegen path is preserved as an escape hatch.
    discovery = None
    if req.source_job_id:
        discovery = DiscoveryResult.model_validate(structure)
    else:
        discovery = DiscoveryResult.model_validate({**structure, 'url': url, 'status': structure.get('status','READY')})
    custom_job_id = job_id or f"custom_{uuid4().hex[:10]}"
    try:
        code, _, _ = await _ensure_codegen(discovery, None, custom_job_id)
    except CodeGenerationError as exc:
        raise HTTPException(status_code=502, detail=f'Custom codegen failed: {exc}') from exc
    crawler_path = Path('generated') / artifact_store.host(url) / f'job_{custom_job_id}' / '09_crawler.py'
    crawler_path.parent.mkdir(parents=True, exist_ok=True)
    crawler_path.write_text(code, encoding='utf-8')
    output = crawler_path.parent / '11_crawl_results.jsonl'
    try:
        run_info = run_crawler(crawler_path, url=url, output=output, max_pages=req.max_pages, timeout_seconds=req.timeout_seconds, headless=req.headless)
    except CrawlerRunError as exc:
        raise HTTPException(status_code=502, detail=f'Custom crawler execution failed: {exc}') from exc
    saved = artifact_store.save_crawl(job_id=custom_job_id, url=url, run_info=run_info)
    return {'job_id': custom_job_id, 'url': url, 'mode':'custom', 'runtime': discovery.method, 'record_count':run_info['record_count'], 'success_count':run_info['success_count'], 'failed_count':run_info['failed_count'], 'records':run_info['records'], 'files':sorted(set(saved)),'result_file':str(output)}


# Compatibility endpoint; new UI no longer uses approval as a workflow gate.
@app.post('/confirm/{job_id}', response_model=Job)
async def confirm(job_id: str, req: ConfirmRequest):
    try:
        job = store.get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail='Unknown job') from exc
    if not req.approved:
        return job
    if job.discovery.status == 'FAILED':
        raise HTTPException(status_code=409, detail='Discovery failed')
    result = await generate_code(CodegenRequest(source_job_id=job.id, structure=job.discovery.model_dump(), url=job.discovery.url))
    job.confirmed = True
    job.generated_files = result['files']
    store.put(job)
    return job
