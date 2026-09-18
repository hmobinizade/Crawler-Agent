# Adaptive Crawler Studio v20

v20 converts the project to a **Generic Crawler Runtime + Extraction Structure** architecture.

## Core idea

The analysis agent discovers an `Extraction Structure`. That structure becomes the executable configuration for one reusable crawler runtime. We no longer generate a near-identical Python crawler for every site by default.

```text
URL + template
   -> Discovery / Analysis
   -> Extraction Structure
   -> Generic Crawler Runtime
   -> JSONL results
```

Custom crawler code generation remains available as an explicit escape hatch:

```text
Extraction Structure
   -> Custom Codegen
   -> custom crawler.py
```

## UI

- `/` — Discover: URL + template -> analysis + saved artifacts + reusable structure
- `/codegen` — Custom Codegen: optional site-specific crawler generation
- `/crawler` — Crawler: run a reusable structure against a start URL
- `/generated` — Artifacts: browse, preview, download, and ZIP generated folders

## Generic runtime API

`POST /crawl` accepts one of:

- `source_job_id`
- `structure_id`
- inline `structure` + `url`

Default mode is `generic`. Use `mode="custom"` only for the explicit codegen escape hatch.

## Structure registry

Analyzed structures are also registered under:

```text
generated/<domain>/structures/<structure_id>.json
```

Use:

- `GET /structures`
- `GET /structures/{structure_id}`

## Generic runtime

`app/generic_runtime.py` supports:

- Requests + BeautifulSoup
- Playwright
- CSS / XPath / text / meta / attribute / JSON-LD extraction
- selector candidates in order
- scope selectors
- collection extraction with `item_fields`
- `@href`, `@src`, and `text` item-field shorthands
- body cleaning
- same-site and page-pattern crawling
- per-record extraction trace

The runtime is the only default crawler implementation. It can be reused for many structures without producing duplicate crawler files.

## Validation

The project includes tests for:

- generic runtime reuse across multiple pages
- structure registry round-trip
- existing collection/codegen regressions
- MCP / analysis wiring

Install and run:

```powershell
pip install -r requirements.txt
pytest -q
uvicorn app.main:app --reload
```
