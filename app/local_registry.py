from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


class LocalCrawlerRegistry:
    def __init__(self, root: str = "generated") -> None:
        self.root = Path(root)

    def host_dir(self, url: str) -> Path:
        host = urlparse(url).netloc.lower().split(":", 1)[0]
        host = re.sub(r"[^a-zA-Z0-9._-]+", "_", host) or "unknown-host"
        return self.root / host

    def candidates(self, url: str) -> list[Path]:
        folder = self.host_dir(url)
        if not folder.exists():
            return []
        generated = list(folder.rglob("09_crawler.py"))
        legacy = list(folder.rglob("*.schema.json"))
        scripts = generated[:]
        for schema in legacy:
            script = schema.with_suffix("").with_suffix(".py")
            if script.exists() and script not in scripts:
                scripts.append(script)
        scripts.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return scripts

    def run(self, url: str, template: dict, timeout: int = 45) -> dict | None:
        for script in self.candidates(url):
            if not script.exists():
                continue
            try:
                proc = subprocess.run(
                    [sys.executable, str(script), "--url", url, "--once"],
                    cwd=str(script.parent), capture_output=True, text=True,
                    timeout=timeout, env=os.environ.copy(),
                )
            except Exception:
                continue
            lines = [x.strip() for x in proc.stdout.splitlines() if x.strip()]
            for line in reversed(lines):
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                data = item.get("data") if isinstance(item, dict) else None
                if isinstance(data, dict):
                    return {
                        "script": str(script),
                        "data": data,
                        "missing": item.get("missing_required", []),
                        "usable": bool(item.get("extraction_ok")),
                        "stdout": proc.stdout[-1000:],
                    }
        return None
