from __future__ import annotations

import json
from urllib.parse import urlparse

from .llm import OpenAICompatibleLLM
from .models import DiscoveryResult, FieldPlan
from .static_extractor import StaticExtractor
from .host_registry import HostAnalysisRegistry
from .local_registry import LocalCrawlerRegistry



def infer_type(value):
    if isinstance(value, bool): return "boolean"
    if isinstance(value, (int,float)): return "number"
    if isinstance(value, list): return "array"
    if isinstance(value, dict): return "object"
    if value is None: return "null"
    return "string"


def flatten_template(value, prefix=""):
    out=[]
    for key, child in value.items():
        path=f"{prefix}.{key}" if prefix else key
        if isinstance(child, dict) and set(child).issubset({"value","required","type","description"}) and "value" in child:
            raw=child.get("value"); out.append({"name":key,"path":path,"value_type":child.get("type") or infer_type(raw),"required":bool(child.get("required",True)),"description":str(child.get("description","") )})
        elif isinstance(child, dict): out.extend(flatten_template(child,path))
        else: out.append({"name":key,"path":path,"value_type":infer_type(child),"required":True,"description":""})
    return out

CLASSIFY_SYSTEM = r"""
You are a routing agent for a web extraction system.
Choose the cheapest reliable method for extracting an already-fetched page:
- requests_bs4: the meaningful fields are present in server HTML / meta / JSON-LD and do not require browser execution.
- playwright: content is rendered/injected by JavaScript, requires interaction/scrolling, or static extraction misses required fields.
Return only JSON: {"method":"requests_bs4|playwright","confidence":0.0,"reason":"..."}
Never choose requests_bs4 when a required field is missing from the static result.
"""


class AdaptiveAgent:
    def __init__(self, registry: HostAnalysisRegistry | None = None, local_registry: LocalCrawlerRegistry | None = None) -> None:
        self.static = StaticExtractor()
        self.llm = OpenAICompatibleLLM()
        self.browser_agent = None
        self.registry = registry or HostAnalysisRegistry()
        self.local_registry = local_registry or LocalCrawlerRegistry()
        self.last_static_html: str | None = None
        self.last_routing_prompt: str | None = None

    async def _classify(self, static: dict, contract: list[dict]) -> tuple[str, str]:
        evidence = {
            "status_code": static.get("status_code"),
            "content_type": static.get("content_type"),
            "static_score": static.get("static_score"),
            "challenge": static.get("challenge"),
            "page_title": static.get("page_title"),
            "missing_required": static.get("missing"),
            "fields": [
                {
                    "path": f.path,
                    "found": f.found,
                    "selector": f.selector,
                    "selector_type": f.selector_type,
                    "value_preview": str(f.value)[:160] if f.value is not None else None,
                }
                for f in static.get("fields", [])
            ],
        }
        prompt = (
            "TEMPLATE=" + json.dumps(contract, ensure_ascii=False, separators=(",", ":"))
            + "\nPREFLIGHT=" + json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
        )
        self.last_routing_prompt = CLASSIFY_SYSTEM + "\n\n" + prompt
        try:
            result = await self.llm.chat_json(CLASSIFY_SYSTEM, prompt)
            method = result.get("method") if result.get("method") in {"requests_bs4", "playwright"} else None
            reason = str(result.get("reason") or "")
            if method:
                return method, reason
        except Exception:
            pass
        if static.get("usable"):
            return "requests_bs4", "All required fields were available in server HTML/metadata."
        return "playwright", "Static preflight could not satisfy the complete template."

    @staticmethod
    def _host(url: str) -> str:
        return urlparse(url).netloc.lower().split(":", 1)[0]

    async def inspect(self, url: str, template: dict, allow_playwright: bool = True) -> DiscoveryResult:
        contract = flatten_template(template)
        host = self._host(url)
        trace: list[str] = []

        # 1) Local site-specific crawler gets the first chance.
        if self.registry.has_host(url):
            raise RuntimeError(f"HOST_ALREADY_ANALYZED: {host}")

        local = self.local_registry.run(url, template)
        if local and local.get("usable"):
            schema_path = local.get("script", "").replace(".py", ".schema.json")
            try:
                import json as _json
                schema = _json.loads(open(schema_path, "r", encoding="utf-8").read())
            except Exception:
                schema = None
            if isinstance(schema, dict):
                discovery = DiscoveryResult.model_validate(schema)
                discovery.source = "local"
                discovery.method = "local"
                discovery.local_crawler = local.get("script")
                discovery.proposed_json = local.get("data") or discovery.proposed_json
                discovery.missing_required = local.get("missing") or []
                discovery.status = "READY" if not discovery.missing_required else "INCOMPLETE"
                discovery.trace = ["Local crawler matched host and returned a usable result."]
                return discovery
            trace.append("Local crawler returned data but its schema could not be loaded; continuing.")
        else:
            trace.append("No usable local crawler matched this host.")

        # 2) Cheap HTTP + BeautifulSoup preflight.
        try:
            static = await self.static.inspect(url, contract)
            self.last_static_html = static.get('html')
            trace.append(f"Static preflight: score={static['static_score']}, missing={static['missing']}.")
            method, reason = await self._classify(static, contract)
        except Exception as exc:
            static = None
            method, reason = "playwright", f"Static preflight failed: {exc}"
            trace.append("Static preflight failed; routing to Playwright.")

        if static and static.get("usable") and method == "requests_bs4":
            fields: list[FieldPlan] = static["fields"]
            return DiscoveryResult(
                status="READY",
                host=host,
                url=str(static["url"]),
                page_title=static.get("page_title", ""),
                anti_bot_state="CHALLENGE" if static.get("challenge") else "NONE",
                method="requests_bs4",
                method_reason=reason,
                source="static",
                template=template,
                proposed_json=static["data"],
                fields=fields,
                missing_required=static["missing"],
                evidence=static.get("notes", []),
                crawler_notes=["Generated crawler will use HTTP + BeautifulSoup; no browser is required."],
                trace=trace + ["Agent selected requests+BeautifulSoup because all required fields are statically available."],
            )

        # 3) Browser mode. When allow_playwright=False we stop here and let the
        # interactive UI open a persistent browser session for the human.
        trace.append(f"Routing decision: {method}. {reason}")
        if not allow_playwright:
            return DiscoveryResult(
                status="NEEDS_HUMAN",
                host=host,
                url=url,
                page_title=static.get("page_title", "") if static else "",
                anti_bot_state="CHALLENGE" if static and static.get("challenge") else "NONE",
                method="playwright",
                method_reason=reason or "Browser discovery selected.",
                source="none",
                template=template,
                proposed_json={},
                fields=[],
                missing_required=[],
                evidence=[],
                crawler_notes=["An interactive browser session must be prepared by a human before analysis."],
                trace=trace,
            )

        from .extractor import ExtractorAgent
        self.browser_agent = ExtractorAgent()
        browser_result = None
        try:
            browser_result = await self.browser_agent.inspect(url, template)
        finally:
            await self.browser_agent.browser.close()
        browser_result.method = "playwright"
        browser_result.source = "playwright"
        browser_result.method_reason = reason or "Dynamic/browser discovery selected."
        browser_result.trace = trace + browser_result.trace
        return browser_result
