# Standalone Playwright Structure Crawler

A small, reusable crawler runtime that executes an **Extraction Structure** against any start URL.

It is intentionally independent from Agentic Crawler Studio: no FastAPI, no LLM, no MCP client, no database.

## Quick start

```powershell
pip install -r requirements.txt
playwright install chromium
python playwright_structure_crawler.py \
  --url "https://example.com/article/123" \
  --structure "structure.json" \
  --out "results.jsonl"
```

To see the browser:

```powershell
python playwright_structure_crawler.py \
  --url "https://example.com/article/123" \
  --structure "structure.json" \
  --out "results.jsonl" \
  --headed
```

## Input contract

The crawler needs only two things:

1. `--url` — the page where crawling starts.
2. `--structure` — the Extraction Structure JSON produced by the analysis stage.

The runtime is **Playwright-only**. There is no Requests/BeautifulSoup mode in this standalone crawler.

## Generic vs custom

A generic structure can contain:

```json
{
  "execution": {
    "mode": "generic"
  }
}
```

Custom crawlers are an application-level escape hatch and are not part of this standalone generic runtime.

## What is supported?

- CSS selectors
- XPath
- text matching
- meta extraction
- JSON-LD extraction
- selector candidates in order
- scope selectors
- collection fields
- `@href`, `@src`, `@attribute`, and `text` item-field shorthands
- full article-body extraction and ad/related-content cleanup
- same-site crawling
- page-pattern filtering
- smooth bottom-to-top scrolling for lazy content
- per-field extraction trace
- JSONL output

## Example

```powershell
python playwright_structure_crawler.py `
  --url "https://farsnews.ir/..." `
  --structure "farsnews_article.json" `
  -- 50 `
  --timeout 600 `
  --out "farsnews_results.jsonl"
```

The structure stays separate from the runtime. That means the same crawler file can execute many different site structures without generating duplicate crawler code.


## Single-page execution contract

The Crawler workflow accepts exactly one URL and one Extraction Structure. It opens that URL once with Playwright, scrolls to the bottom to trigger lazy-loaded content, returns to the top, extracts the requested fields, and returns one JSON record. It does not follow links or paginate.
