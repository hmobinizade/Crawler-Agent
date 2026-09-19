# Adaptive Crawler Studio v25

A Playwright-first, multi-round agentic web extraction system for turning a page URL + extraction template into a verified reusable `Extraction Structure`.

The design goal is **high selector accuracy without sending the whole page source to the LLM** and without allowing an unbounded browser-tool loop.

---

## Architecture

```text
URL + JSON Template
        │
        ▼
   Playwright MCP
        │
        ├── Navigate
        ├── Gradual scroll to bottom
        └── Return to top
        │
        ▼
┌───────────────────────────────┐
│ Round 1 — Recon               │
│ LLM + bounded browser tools   │
└───────────────┬───────────────┘
                ▼
┌───────────────────────────────┐
│ Round 2 — Field Investigation │
│ targeted browser inspection   │
└───────────────┬───────────────┘
                ▼
┌───────────────────────────────┐
│ Round 3 — Deterministic       │
│ selector verification         │
└───────────────┬───────────────┘
                ▼
┌───────────────────────────────┐
│ Round 4 — Critic + Synthesis  │
│ LLM, no browser tools         │
└───────────────┬───────────────┘
                ▼
      Weak required fields?
          │          │
         No         Yes
          │          ▼
          │   Round 5 — Repair
          │   bounded browser tools
          │          │
          └──────┬───┘
                 ▼
        Final Extraction Structure
```

### Why this design?

The browser is the source of truth, not an HTML summary and not an LLM-generated guess.

The LLM can inspect the live page through a **small, bounded tool budget**:

- Round 1: up to 2 LLM/tool rounds.
- Round 2: up to 3 LLM/tool rounds.
- Round 4: 1 normal JSON call, no browser tools.
- Round 5: only when required fields remain weak/missing, up to 2 LLM/tool rounds.

Only these MCP browser tools are exposed during analysis:

- `browser_snapshot`
- `browser_evaluate`

Navigation/reload/back/forward/close are never exposed to the analysis agent.

This keeps the analysis powerful enough to inspect the DOM deeply while preventing the old unbounded tool-loop/token-spend problem.

---

## What the LLM actually sees

The system **does not send the complete raw HTML source to the LLM**.

Instead:

```text
Playwright
   ↓
fully rendered page
   ↓
LLM can inspect the live DOM through targeted browser tools
   ↓
only the relevant evidence returned by those tools enters the LLM context
```

The complete rendered source is still captured and saved as an artifact for debugging, reproducibility, and later code generation.

---

## Analysis rounds

### Round 1 — Recon

The agent builds a compact page map:

- page type
- main article/content regions
- metadata sources
- JSON-LD
- repeated collections
- comments/replies
- related-content regions
- special behaviors
- anti-bot/login indicators

It should inspect the page, not write the final selector map yet.

### Round 2 — Field Investigation

Every requested field from the JSON template is investigated independently.

Examples:

```text
title → h1 / JSON-LD headline
body → article container + paragraph density
image → metadata / JSON-LD / visible hero image
date → meta / JSON-LD / visible time element
comments → repeated comment items + child fields
news_urls → repeated anchors + @href + text
```

Collections are treated as collections. `@href`, `@src`, `text`, nested selectors, and JSON-LD arrays are supported.

### Round 3 — Deterministic verification

The Python side executes the proposed selectors against the real browser DOM.

A selector is not accepted merely because the LLM proposed it.

We check:

- selector execution
- uniqueness/cardinality
- extracted value
- scope
- collection count
- item fields
- JSON-LD shape

### Round 4 — Critic + Synthesis

The model receives only the compact results from the previous rounds and reconciles them into the final `Extraction Structure`.

### Round 5 — Targeted Repair

Only fields that remain missing, ambiguous, or weak are investigated again. The entire page is not re-analyzed.

---

## Live progress UI

The Discover page now runs analysis asynchronously and polls a lightweight progress endpoint.

Stages are shown live:

```text
Browser → Scroll → Recon → Fields → Synthesis → Verify → Repair → Done
```

Tool calls and tool results are also logged with the stage that produced them.

API endpoints:

```http
POST /analysis/start
GET  /analysis/{run_id}
```

The older synchronous endpoint remains available for compatibility:

```http
POST /discover
```

---

## Artifacts

Every analysis is saved under:

```text
generated/<domain>/job_<id>/
```

Typical files:

```text
01_template.json
02_extraction_structure.json
03_discovery_prompt.txt
04_source_page.html
05_analysis_response.json
06_round1_recon_prompt.txt
07_round1_recon.json
08_round2_field_prompt.txt
09_round2_investigation.json
10_round4_synthesis_prompt.txt
11_round4_synthesis.json
12_round5_repair_prompt.txt
13_round5_repair.json
14_verification.json
15_final_structure.json
manifest.json
```

The browser profile is not exposed through the artifact browser or ZIP downloads.

---

## Configuration

Example `.env`:

```env
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=YOUR_API_KEY
LLM_MODEL=deepseek-flash
LLM_TIMEOUT_SECONDS=180
LLM_MAX_TOKENS=6000
LLM_SEED=42

MCP_MODE=stdio
MCP_COMMAND=npx
MCP_ARGS=["@playwright/mcp@latest","--user-data-dir",".browser-profile"]
```

For a one-million-token context deployment, the application does not locally truncate the saved HTML. The HTML is stored for artifacts, while the active LLM context is controlled by the bounded browser-tool rounds.

---

## Run locally

```powershell
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open:

```text
http://127.0.0.1:8000/
```

Make sure Playwright MCP is available in the configured transport.

---

## Design principles

**Browser truth.** DOM verification beats model confidence.

**Field-specific investigation.** A date, article body, image and collection should not all be inspected the same way.

**Bounded agent loops.** Every LLM/browser phase has a hard round limit.

**Deterministic verification.** The LLM proposes; the browser proves.

**Repair, not regeneration.** When something fails, inspect only the weak fields instead of restarting the whole analysis.

**Reusable structure.** The final output is an `Extraction Structure` that can be executed by the generic Playwright runtime.

**Artifacts are first-class.** Prompts, source HTML, round outputs and verification results are preserved for debugging and reproducibility.
### Tool-loop safety

Some OpenAI-compatible providers may ignore `tool_choice=none` on the final round and still return a tool call. The LLM client never executes a tool after the budget is exhausted; it performs one bounded no-tools synthesis request using the accumulated conversation instead.
