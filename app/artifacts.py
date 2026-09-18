from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse
from typing import Any


class ArtifactStore:
    def __init__(self, root: str = "generated") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def host(url: str) -> str:
        value = urlparse(url).netloc.lower().split(":", 1)[0]
        return re.sub(r"[^A-Za-z0-9._-]+", "_", value) or "unknown-host"

    def job_dir(self, url: str, job_id: str) -> Path:
        path = self.root / self.host(url) / f"job_{job_id}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def write_text(path: Path, content: str) -> str:
        path.write_text(content or "", encoding="utf-8")
        return str(path)

    @staticmethod
    def write_json(path: Path, value: Any) -> str:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)

    def save_discovery(
        self,
        *,
        job_id: str,
        url: str,
        template: dict[str, Any],
        discovery: Any,
        source_html: str | None = None,
        routing_prompt: str | None = None,
        discovery_prompt: str | None = None,
        compact_evidence: str | None = None,
        analysis_response: dict[str, Any] | None = None,
    ) -> list[str]:
        directory = self.job_dir(url, job_id)
        files: list[str] = []
        files.append(self.write_json(directory / "01_template.json", template))
        files.append(self.write_json(directory / "02_extraction_structure.json", discovery.model_dump(mode="json") if hasattr(discovery, "model_dump") else discovery))
        if routing_prompt:
            files.append(self.write_text(directory / "03_routing_prompt.txt", routing_prompt))
        if discovery_prompt:
            files.append(self.write_text(directory / "04_discovery_prompt.txt", discovery_prompt))
        if compact_evidence:
            files.append(self.write_text(directory / "05_compact_dom_evidence.txt", compact_evidence))
        if analysis_response is not None:
            files.append(self.write_json(directory / "06_analysis_response.json", analysis_response))
        if source_html:
            files.append(self.write_text(directory / "06_source_page.html", source_html))
        manifest = {
            "job_id": job_id,
            "url": url,
            "host": self.host(url),
            "method": getattr(discovery, 'method', None) if hasattr(discovery, 'method') else (discovery.get('method') if isinstance(discovery, dict) else None),
            "status": getattr(discovery, 'status', None) if hasattr(discovery, 'status') else (discovery.get('status') if isinstance(discovery, dict) else None),
            "artifacts": [str(Path(p).name) for p in files],
        }
        files.append(self.write_json(directory / "manifest.json", manifest))
        return files

    def save_codegen(
        self,
        *,
        job_id: str,
        url: str,
        code: str,
        codegen_prompt: str,
        llm_response: dict[str, Any],
        validation: dict[str, Any],
    ) -> list[str]:
        directory = self.job_dir(url, job_id)
        files: list[str] = []
        files.append(self.write_text(directory / "07_codegen_prompt.txt", codegen_prompt))
        files.append(self.write_json(directory / "08_codegen_llm_response.json", llm_response))
        files.append(self.write_text(directory / "09_crawler.py", code))
        files.append(self.write_json(directory / "10_codegen_validation.json", validation))
        manifest_path = directory / "manifest.json"
        manifest: dict[str, Any] = {}
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                manifest = {}
        manifest.setdefault("artifacts", [])
        manifest["artifacts"] = sorted(set(manifest["artifacts"] + [Path(p).name for p in files]))
        files.append(self.write_json(manifest_path, manifest))
        return files

    def read_source_html(self, url: str, job_id: str) -> str | None:
        path = self.job_dir(url, job_id) / "06_source_page.html"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    def read_structure(self, url: str, job_id: str) -> dict[str, Any] | None:
        path = self.job_dir(url, job_id) / "02_extraction_structure.json"
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except Exception:
            return None

    def safe_resolve(self, relative_path: str) -> Path:
        raw = Path(relative_path)
        if raw.is_absolute() or '..' in raw.parts:
            raise ValueError('Invalid artifact path')
        base = self.root.resolve()
        target = (self.root / raw).resolve()
        if target != base and base not in target.parents:
            raise ValueError('Invalid artifact path')
        return target

    def tree(self) -> list[dict[str, Any]]:
        def node_for(path: Path, rel: Path) -> dict[str, Any]:
            if path.is_dir():
                children = [node_for(child, rel / child.name) for child in sorted(path.iterdir(), key=lambda x: (x.is_file(), x.name.lower())) if child.name != '.browser-profile']
                return {'type': 'folder', 'name': path.name, 'path': str(rel).replace('\\','/'), 'children': children}
            stat = path.stat()
            return {
                'type': 'file',
                'name': path.name,
                'path': str(rel).replace('\\','/'),
                'size': stat.st_size,
                'modified': stat.st_mtime,
                'parent_path': str(rel.parent).replace('\\','/') if str(rel.parent) != '.' else '',
                'download': f'/artifacts/download?path={str(rel).replace(chr(92), "/")}',
            }
        if not self.root.exists():
            return []
        return [node_for(child, Path(child.name)) for child in sorted(self.root.iterdir(), key=lambda x: (x.is_file(), x.name.lower())) if child.name not in {'.host_registry.json', '.browser-profile'}]


artifact_store = ArtifactStore()

# Extended helpers are defined as methods below by monkey-patching for backward compatibility.
def _job_entries(self):
    items = []
    if not self.root.exists():
        return items
    for domain_dir in sorted([p for p in self.root.iterdir() if p.is_dir() and p.name != '.browser-profile']):
        for job_dir in sorted([p for p in domain_dir.iterdir() if p.is_dir() and p.name.startswith('job_')], key=lambda p: p.stat().st_mtime, reverse=True):
            manifest = {}
            mp = job_dir / 'manifest.json'
            if mp.exists():
                try: manifest = json.loads(mp.read_text(encoding='utf-8'))
                except Exception: manifest = {}
            structure = job_dir / '02_extraction_structure.json'
            crawler = job_dir / '09_crawler.py'
            results = job_dir / '11_crawl_results.jsonl'
            items.append({
                'domain': domain_dir.name,
                'job_id': job_dir.name.removeprefix('job_'),
                'folder': str(job_dir.relative_to(self.root)).replace('\\','/'),
                'url': manifest.get('url') or '',
                'method': (manifest.get('method') or ''),
                'has_structure': structure.exists(),
                'has_crawler': crawler.exists(),
                'has_results': results.exists(),
                'modified': job_dir.stat().st_mtime,
            })
    return items


def _job_entry(self, job_id: str):
    for item in _job_entries(self):
        if item['job_id'] == job_id:
            return item
    return None


def _job_dir_by_id(self, job_id: str) -> Path | None:
    item = _job_entry(self, job_id)
    if not item:
        return None
    return self.root / item['folder']


def _read_file(self, job_id: str, filename: str) -> str | None:
    directory = _job_dir_by_id(self, job_id)
    if not directory:
        return None
    path = directory / filename
    return path.read_text(encoding='utf-8', errors='replace') if path.exists() else None


def _save_crawl(self, *, job_id: str, url: str, run_info: dict[str, Any]) -> list[str]:
    directory = self.job_dir(url, job_id)
    files = []
    records = run_info.get('records') or []
    output = directory / '11_crawl_results.jsonl'
    with output.open('w', encoding='utf-8') as f:
        for row in records:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
    files.append(str(output))
    files.append(self.write_json(directory / '12_crawl_run.json', run_info))
    manifest_path = directory / 'manifest.json'
    manifest = {}
    if manifest_path.exists():
        try: manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        except Exception: manifest = {}
    manifest['crawl'] = {
        'record_count': run_info.get('record_count', 0),
        'success_count': run_info.get('success_count', 0),
        'failed_count': run_info.get('failed_count', 0),
        'output': '11_crawl_results.jsonl',
    }
    manifest.setdefault('artifacts', [])
    manifest['artifacts'] = sorted(set(manifest['artifacts'] + [Path(p).name for p in files]))
    files.append(self.write_json(manifest_path, manifest))
    return files

ArtifactStore.job_entries = _job_entries
ArtifactStore.job_entry = _job_entry
ArtifactStore.job_dir_by_id = _job_dir_by_id
ArtifactStore.read_file = _read_file
ArtifactStore.save_crawl = _save_crawl
