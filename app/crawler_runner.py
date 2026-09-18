from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


class CrawlerRunError(RuntimeError):
    pass


def run_crawler(script: str | Path, *, url: str, output: str | Path, max_pages: int = 20,
                timeout_seconds: int = 240, headless: bool = True) -> dict[str, Any]:
    script_path = Path(script).resolve()
    output_path = Path(output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env['CRAWLER_START_URL'] = url
    env['CRAWLER_OUT'] = str(output_path)
    env['CRAWLER_MAX_PAGES'] = str(max_pages)
    env['CRAWLER_HEADLESS'] = '1' if headless else '0'
    env['PYTHONUNBUFFERED'] = '1'

    try:
        proc = subprocess.run(
            [sys.executable, str(script_path), '--url', url],
            cwd=str(script_path.parent),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise CrawlerRunError(f'Crawler timed out after {timeout_seconds}s') from exc
    except OSError as exc:
        raise CrawlerRunError(f'Could not start crawler: {exc}') from exc

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or 'Crawler exited with a non-zero status').strip()
        raise CrawlerRunError(err[-8000:])

    records: list[dict[str, Any]] = []
    if output_path.exists():
        for line in output_path.read_text(encoding='utf-8', errors='replace').splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                records.append(item)

    failed = [r for r in records if not r.get('extraction_ok', False)]
    return {
        'records': records[-100:],
        'record_count': len(records),
        'success_count': len(records) - len(failed),
        'failed_count': len(failed),
        'output_path': str(output_path),
        'stdout_tail': (proc.stdout or '')[-5000:],
        'stderr_tail': (proc.stderr or '')[-5000:],
        'returncode': proc.returncode,
    }
