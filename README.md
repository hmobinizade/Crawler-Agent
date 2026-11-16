# Agentic Crawler Studio v29

## Empty-LLM-response recovery

The bounded Playwright-MCP agent now treats an empty provider `content` as a recoverable terminal condition. It performs **one** no-tools JSON-only recovery request using the evidence already collected. It does not restart browser investigation or add tool rounds.

Frontend logs show the recovery explicitly:

- `LLM returned empty content; starting one no-tools recovery...`
- `LLM returned empty content; recovered with one no-tools finalization.`

If the provider returns an empty response again, the run fails with an explicit provider error rather than entering another tool loop.

## DeepSeek thinking/tool-call compatibility

When DeepSeek thinking mode is used with browser tools, the assistant turn must be replayed with `content`, `reasoning_content`, and `tool_calls`. The runtime now preserves those fields on every tool turn and uses a tool-free terminal finalization when the tool budget ends. Empty `content` is therefore treated as recoverable instead of immediately failing.

Configuration:

```env
LLM_THINKING_MODE=auto
LLM_REASONING_EFFORT=high
LLM_TERMINAL_MAX_TOKENS=8000
```

`auto` enables thinking only for the official DeepSeek API host. Use `enabled` explicitly when a compatible OpenAI-style provider supports DeepSeek thinking mode.

## Extraction priority and runtime guarantees

The discovery/runtime path is JSON-LD-first. Before visible-DOM investigation, all `script[type="application/ld+json"]` blocks are parsed, including arrays and `@graph` objects. When JSON-LD contains the requested value, the structure uses `selector_type: "jsonld"` and an executable property path such as `headline` or `author.name`; explanatory strings inside selectors are normalized away.

Visible CSS/XPath/meta rules are used only for fields that JSON-LD does not satisfy. Scalar DOM selectors are not allowed to silently select the first node when multiple nodes match; the runtime requires a unique match or a verified sample-based disambiguation. Semantic fallbacks are disabled by default and are not enabled from model output.
