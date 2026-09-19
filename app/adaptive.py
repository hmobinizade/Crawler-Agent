from __future__ import annotations

from urllib.parse import urlparse
from typing import Any, Awaitable, Callable

from .host_registry import HostAnalysisRegistry
from .local_registry import LocalCrawlerRegistry
from .models import DiscoveryResult




def infer_type(value):
    if isinstance(value, bool): return "boolean"
    if isinstance(value, (int, float)): return "number"
    if isinstance(value, list): return "array"
    if isinstance(value, dict): return "object"
    if value is None: return "null"
    return "string"


def flatten_template(value, prefix=""):
    out=[]
    for key, child in value.items():
        path=f"{prefix}.{key}" if prefix else key
        if isinstance(child, dict) and set(child).issubset({"value","required","type","description","items"}) and "value" in child:
            raw=child.get("value")
            out.append({"name":key,"path":path,"value_type":child.get("type") or infer_type(raw),"required":bool(child.get("required",True)),"description":str(child.get("description","")),"item_schema":child.get("items") or {}})
        elif isinstance(child, dict):
            out.extend(flatten_template(child,path))
        elif isinstance(child, list):
            out.append({"name":key,"path":path,"value_type":"array","required":True,"description":"Collection","item_schema":child[0] if child and isinstance(child[0],dict) else {}})
        else:
            out.append({"name":key,"path":path,"value_type":infer_type(child),"required":True,"description":"","item_schema":{}})
    return out


class AdaptiveAgent:
    """Playwright-only discovery orchestrator.

    The class name is retained for compatibility with the existing API, but discovery
    no longer performs static preflight or LLM routing. Playwright is always the source
    of the observed page, and ExtractorAgent performs the single analysis LLM call.
    """

    def __init__(self, registry: HostAnalysisRegistry | None = None, local_registry: LocalCrawlerRegistry | None = None) -> None:
        self.browser_agent = None
        self.registry = registry or HostAnalysisRegistry()
        # Kept only for compatibility with callers from earlier versions. It is never executed.
        self.local_registry = local_registry or LocalCrawlerRegistry()
        self.last_static_html = None
        self.last_routing_prompt = None

    @staticmethod
    def _host(url: str) -> str:
        return urlparse(url).netloc.lower().split(":", 1)[0]

    async def inspect(self, url: str, template: dict, allow_playwright: bool = True, progress_cb: Callable[[str, str, dict[str, Any] | None], Awaitable[None]] | None = None) -> DiscoveryResult:
        if not allow_playwright:
            raise RuntimeError("Playwright is the only supported discovery path in v24")
        host = self._host(url)
        if self.registry.has_host(url):
            raise RuntimeError(f"HOST_ALREADY_ANALYZED: {host}")

        from .extractor import ExtractorAgent

        self.browser_agent = ExtractorAgent()
        try:
            result = await self.browser_agent.inspect(url, template, navigate=True, progress_cb=progress_cb)
        finally:
            await self.browser_agent.browser.close()

        result.method = "playwright"
        result.source = "playwright"
        result.method_reason = (
            "Playwright-only multi-round discovery: the live page was prepared in the browser, "
            "the LLM inspected targeted browser evidence through bounded MCP tool calls, "
            "then selectors were deterministically verified against the live DOM."
        )
        result.trace = [
            "Playwright MCP was the only browser/data acquisition path.",
            "No static preflight or routing LLM was used.",
            "The page was gradually scrolled to the end and returned to the top.",
            "The LLM used bounded browser_snapshot/browser_evaluate calls in targeted rounds.",
            "A deterministic Playwright verification pass validated the proposed selectors."
        ] + result.trace
        return result
