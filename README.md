# Adaptive Crawler Studio v23

> **Analyze once. Freeze a deterministic structure. Execute it with one reusable Playwright runtime.**

Adaptive Crawler Studio is an agentic web-extraction workspace built around one core idea:

**the Analysis Structure is the reusable asset — not the generated Python file.**

The system discovers an `Extraction Structure` from a real page, stores the evidence used to build it, and then executes that structure through a shared **Playwright crawler runtime**.

For unusual sites, the same structure can opt into a one-off custom Playwright crawler.

---

## ✨ What the system does

| Workflow | Input | Output |
|---|---|---|
| **Discover** | URL + requested fields | Extraction Structure + saved evidence |
| **Codegen** | Structure | Optional custom Playwright crawler |
| **Crawler** | URL + Structure | JSONL extraction results |
| **Files** | Existing artifacts | Preview / download / ZIP |

### The normal path

```text
URL + fields
    │
    ▼
┌──────────────────────┐
│  Discovery / Agent   │
│  Playwright + LLM    │
└──────────┬───────────┘
           │
           ▼
   Extraction Structure
           │
           ▼
┌──────────────────────┐
│ Generic Playwright   │
│ Crawler Runtime      │
└──────────┬───────────┘
           │
           ▼
       JSON / JSONL
```

### The exception path

```text
Extraction Structure
        │
        ▼
   Custom Codegen
        │
        ▼
Custom Playwright crawler
```

The custom path is an **escape hatch**, not the default architecture.

---

## 🧠 Architecture

### 1. Discovery

Discovery receives only:

```text
URL
+
JSON template of requested fields
```

It can use the existing Playwright MCP workflow to open and inspect the page, scroll it, collect compact DOM evidence, and ask the LLM to produce a structured extraction plan.

Saved artifacts include:

```text
generated/<domain>/job_<id>/
├── 01_template.json
├── 02_extraction_structure.json
├── 03_routing_prompt.txt
├── 04_discovery_prompt.txt
├── 05_compact_dom_evidence.txt
├── 06_analysis_response.json
├── 06_source_page.html
└── manifest.json
```

### 2. Extraction Structure

The structure is the reusable contract between analysis and crawling.

A simplified example:

```json
{
  "execution": {
    "mode": "generic"
  },
  "site": {
    "host": "farsnews.ir"
  },
  "crawl": {
    "delay_seconds": 1.5
  },
  "fields": [
    {
      "name": "title",
      "path": "title",
      "required": true,
      "value_type": "string",
      "selector": "h1.article-title",
      "selector_type": "css",
      "selector_candidates": ["h1.article-title", "h1"]
    },
    {
      "name": "date",
      "path": "date",
      "required": true,
      "value_type": "string",
      "selector": "datePublished",
      "selector_type": "jsonld"
    }
  ]
}
```

The runtime respects the analyzed contract exactly. The LLM may propose a selector, but the browser verification phase resolves the real element and rewrites the final Structure with deterministic, unique selectors and verified candidates. The runtime then executes that frozen Structure without asking an LLM to reinterpret it.

Every extraction also records an `extraction_trace` showing the selector/candidate attempts and the rule that actually succeeded.

---

## 🕷️ Crawler runtime: Playwright only

The current crawler layer intentionally has **no Requests/BeautifulSoup execution mode**.

Every generic crawl uses:

```text
Playwright
   ↓
Navigate to one URL
   ↓
Smooth scroll to page bottom
   ↓
Return to top
   ↓
Extract according to Structure
   ↓
Return one JSON record
```

Supported extraction modes include:

- CSS
- XPath
- text matching
- meta tags
- HTML attributes
- JSON-LD scalar values
- JSON-LD arrays and objects
- collection item extraction
- `@href`, `@src`, `@attribute`, `text`
- article-body extraction with ad/related-content cleanup
- selector candidates and scopes
- deterministic selector canonicalization after LLM analysis
- selector match counts and extraction traces

---

## 📦 Standalone generic crawler

The most important deliverable is now independent of the Studio application:

```text
standalone/playwright_structure_crawler.py
```

You can give this file to a client and say:

> "Take any compatible Extraction Structure and run it with this crawler."

It does **not** depend on:

- FastAPI
- the Agentic Crawler Studio
- an LLM
- MCP
- the local database

It only needs Playwright.

### Install

```powershell
cd standalone
pip install -r requirements.txt
playwright install chromium
```

### Run

```powershell
python playwright_structure_crawler.py `
  --url "https://example.com/article/123" `
  --structure "structure.json" `
  --out "results.jsonl"
```

Show the browser:

```powershell
python playwright_structure_crawler.py `
  --url "https://example.com/article/123" `
  --structure "structure.json" `
  --out "results.jsonl" `
  --headed
```

That same crawler can run many different Structures without generating duplicate crawler code.

---

## 🎯 Generic vs custom

### Generic — recommended

```json
{
  "execution": {
    "mode": "generic"
  }
}
```

The shared crawler runtime executes the structure.

### Custom — exceptional cases

```json
{
  "execution": {
    "mode": "custom",
    "script": "farsnews.ir/job_123/09_crawler.py"
  }
}
```

The Studio can execute that specific Playwright crawler instead.

This is useful for sites with genuinely unusual behaviour where a reusable extraction structure is not sufficient.

---

## 🖥️ UI

The application is deliberately split into four focused areas:

### Discover
Analyze a page and produce a reusable structure.

### Codegen
Create a **custom Playwright crawler** only when necessary.

### Crawler
Give it exactly:

```text
URL
+
Extraction Structure
```

The execution mode is read from the Structure.

### Files
Browse saved domain/job artifacts, preview text/JSON, download individual files, or download a complete job/domain bundle as ZIP.

---

## 📁 Artifact layout

```text
generated/
├── farsnews.ir/
│   ├── job_abc123/
│   │   ├── 01_template.json
│   │   ├── 02_extraction_structure.json
│   │   ├── 06_source_page.html
│   │   ├── 09_crawler.py
│   │   ├── 11_crawl_results.jsonl
│   │   └── 12_crawl_run.json
│   └── structures/
│       ├── article_v1.json
│       └── article_v2.json
└── tabnak.ir/
    └── ...
```

Browser profiles are excluded from the artifact browser and ZIP downloads.

---

## 🚀 Run the Studio

```powershell
pip install -r requirements.txt
playwright install chromium
pytest -q
uvicorn app.main:app --reload
```

Open:

```text
http://127.0.0.1:8000/
```

---

## 🔌 Main API

### Discover

```http
POST /discover
```

```json
{
  "url": "https://example.com/article/123",
  "template": {
    "title": "",
    "body": "",
    "date": ""
  }
}
```

### Generic / custom crawl

```http
POST /crawl
```

```json
{
  "url": "https://example.com/article/123",
  "structure": { "...": "..." },
  "timeout_seconds": 240,
  "headless": true
}
```

The endpoint intentionally receives only the **URL + Structure** as the crawling contract.

---

## 🧪 Engineering principles

**One runtime, many structures.** Avoid duplicate crawler code.

**Structure is the contract.** Analysis decisions should survive into execution unchanged.

**Playwright is the current crawler substrate.** No static crawler path is exposed in the crawl workflow.

**LLM is for understanding, not repetition.** Analysis can be agentic; crawling should be deterministic and inspectable.

**Custom is an escape hatch.** Keep exceptional code isolated from the generic runtime.

**Every run leaves evidence.** Results and traces should be debuggable after the fact.

---

## License / handoff

The standalone crawler can be delivered independently together with one or more Extraction Structure JSON files. The Studio itself remains the analysis, structure-management, artifact-management, and custom-crawler workspace.


## Single-page execution contract

The Crawler workflow accepts exactly one URL and one Extraction Structure. It opens that URL once with Playwright, scrolls to the bottom to trigger lazy-loaded content, returns to the top, extracts the requested fields, and returns one JSON record. It does not follow links or paginate.
