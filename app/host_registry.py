from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse


class HostAlreadyAnalyzed(RuntimeError):
    pass


class HostAnalysisRegistry:
    """Persistent one-time analysis registry keyed by hostname."""

    def __init__(self, root: str = "generated") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / ".host_registry.json"

    @staticmethod
    def normalize_host(url: str) -> str:
        host = urlparse(url).netloc.lower().split(":", 1)[0]
        return re.sub(r"[^a-zA-Z0-9._-]+", "_", host) or "unknown-host"

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save(self, data: dict) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def has_host(self, url: str) -> bool:
        host = self.normalize_host(url)
        if host in self._load():
            return True
        # Backward compatibility: an existing generated schema means the host was already analyzed.
        folder = self.root / host
        return folder.exists() and (any(folder.rglob('09_crawler.py')) or any(folder.rglob('*.schema.json')))

    def mark(self, url: str, job_id: str, method: str) -> None:
        data = self._load()
        data[self.normalize_host(url)] = {"job_id": job_id, "method": method}
        self._save(data)
