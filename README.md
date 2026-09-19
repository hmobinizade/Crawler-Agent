# Adaptive Crawler Studio v26

## What changed

The analysis path now combines the strongest part of the old v10 design—letting the LLM inspect the current Playwright page directly—with strict deterministic verification and a 95% retry gate.

### Analysis flow

```text
URL + Template
  -> Playwright navigation + human-speed scroll
  -> bounded LLM browser investigation (max 4 rounds / 9 tool calls)
  -> strict selector verification (no semantic fallback)
  -> deterministic confidence score
  -> if any required field < 95%: one targeted repair pass (max 2 rounds / 4 tool calls)
  -> final Extraction Structure
```

### Anti-hallucination rules

- LLM `confidence` is not trusted as evidence.
- A field is accepted only when the proposed selector/path is reproduced by the live browser.
- Unverified values are discarded.
- Weak required fields remain `INCOMPLETE` instead of being invented.
- Selector candidates are alternative rules for the same verified value.

### Bounded MCP usage

The analysis agent can use only `browser_snapshot`, `browser_evaluate`, and same-page `browser_click`. Navigation/reload/back/forward are blocked. The global tool budget is enforced by the LLM client.

### Runtime

The crawler side remains Playwright-only. The reusable generic runtime executes a URL against an Extraction Structure. Custom codegen is reserved for exceptional sites.

### Configuration

```env
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-flash
LLM_DISCOVERY_MAX_ROUNDS=4
LLM_DISCOVERY_MAX_TOOL_CALLS=9
LLM_REPAIR_MAX_ROUNDS=2
LLM_REPAIR_MAX_TOOL_CALLS=4
LLM_CONFIDENCE_THRESHOLD=0.95
```
