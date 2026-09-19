from __future__ import annotations

import json
import re
import pprint
from pathlib import Path
from typing import Any

from .models import DiscoveryResult


STANDALONE_TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "standalone"
    / "playwright_structure_crawler.py"
)


def _clean_host(host: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", host) or "unknown-host"


def _build_embedded_structure(discovery: DiscoveryResult) -> dict[str, Any]:
    structure = {
        "execution": {"mode": "generic"},
        "site": {"host": discovery.host, "url": discovery.url},
        "url": discovery.url,
        "template": discovery.template,
        "fields": [field.model_dump(mode="json") for field in discovery.fields],
        "crawl": {
            "delay_seconds": 1.5,
            "navigation_timeout_ms": 60000,
            "page_timeout_ms": 8000,
        },
    }
    return structure


def _render_standalone(structure: dict[str, Any], start_url: str, output: str) -> str:
    source = STANDALONE_TEMPLATE.read_text(encoding="utf-8")
    source = source.replace("import json\n", "import json\nimport os\n", 1)

    marker = "\n\n# ---------------------------------------------------------------------------\n# Generic helpers\n# ---------------------------------------------------------------------------\n"
    embedded = (
        "\n\n# Embedded extraction contract. This file is standalone and does not depend on Studio.\n"
        f"EMBEDDED_STRUCTURE = {pprint.pformat(structure, width=120, sort_dicts=False)}\n"
        "FIELDS = EMBEDDED_STRUCTURE['fields']\n"
        f"START_URL = {start_url!r}\n"
        f"DEFAULT_OUT = {output!r}\n"
    )
    source = source.replace(marker, embedded + marker, 1)

    main_start = source.index("def main() -> None:\n")
    replacement = '''def main() -> None:\n    import argparse\n\n    parser = argparse.ArgumentParser(description="Standalone Playwright Structure Crawler")\n    parser.add_argument("--url", default=os.getenv("CRAWLER_START_URL", START_URL))\n    parser.add_argument("--out", default=os.getenv("CRAWLER_OUT", DEFAULT_OUT))\n    parser.add_argument("--timeout", type=int, default=240)\n    parser.add_argument("--headed", action="store_true", help="Show the browser window")\n    parser.add_argument("--once", action="store_true", help="Execute exactly one page (default behavior)")\n    args = parser.parse_args()\n\n    result = run_structure(\n        EMBEDDED_STRUCTURE,\n        start_url=args.url,\n        output=args.out,\n        timeout_seconds=args.timeout,\n        headless=not args.headed,\n    )\n    print(json.dumps(result, ensure_ascii=False, indent=2))\n\n\nif __name__ == "__main__":\n    main()\n'''
    source = source[:main_start] + replacement
    return source


def generate(job_id: str, discovery: DiscoveryResult, out_dir: str = "generated") -> list[str]:
    host = _clean_host(discovery.host)
    folder = Path(out_dir) / host / f"job_{job_id}"
    folder.mkdir(parents=True, exist_ok=True)

    structure = _build_embedded_structure(discovery)
    crawler_path = folder / "09_crawler.py"
    output_path = folder / "11_crawl_results.jsonl"
    crawler_code = _render_standalone(structure, discovery.url, str(output_path))
    crawler_path.write_text(crawler_code, encoding="utf-8")

    schema_path = folder / "crawler.schema.json"
    schema_path.write_text(
        json.dumps(structure, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return [str(crawler_path), str(schema_path)]
