import asyncio
import logging

import pytest

from app.extractor import AGENT_BROWSER_TOOLS, ExtractorAgent
from app.mcp_client import (
    MCPToolError,
    build_mcp_subprocess_env,
    describe_browser_launch,
    interpret_call_result,
    unwrap_mcp_payload,
)


class Tool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = name
        self.inputSchema = {"type": "object", "properties": {}}


class Text:
    def __init__(self, text: str) -> None:
        self.text = text


class CallResult:
    def __init__(self, text: str, is_error: bool = False) -> None:
        self.content = [Text(text)]
        self.isError = is_error
        self.structuredContent = None


class ScriptedBrowser:
    def __init__(self, *, fail_navigation: bool = False, blank: bool = False) -> None:
        self.fail_navigation = fail_navigation
        self.blank = blank
        self.calls: list[tuple[str, dict]] = []
        self.session = object()

    async def list_tools(self):
        return [
            Tool("browser_navigate"),
            Tool("browser_snapshot"),
            Tool("browser_evaluate"),
            Tool("browser_click"),
            Tool("browser_wait_for"),
            Tool("browser_close"),
            Tool("browser_navigate_back"),
        ]

    async def call(self, name, args=None):
        args = args or {}
        self.calls.append((name, args))
        if name == "browser_navigate":
            if self.fail_navigation:
                raise MCPToolError("browser_navigate", "browser executable failed to launch")
            if self.blank:
                return "### Page\n- Page URL: about:blank\n- Page Title: \n### Snapshot\n```yaml\n- generic\n```"
            return (
                "### Page\n- Page URL: https://example.com/\n- Page Title: Example Domain\n"
                "### Snapshot\n```yaml\n- heading \"Example Domain\"\n```"
            )
        if name == "browser_snapshot":
            if self.blank:
                return "### Page\n- Page URL: about:blank\n- Page Title: \n### Snapshot\n```yaml\n- generic\n```"
            return (
                "### Page\n- Page URL: https://example.com/\n- Page Title: Example Domain\n"
                "### Snapshot\n```yaml\n- heading \"Example Domain\" [ref=e1]\n"
                "- paragraph: This domain is for use in documentation examples.\n```"
            )
        if name == "browser_evaluate":
            fn = str(args.get("function") or "")
            if self.blank and ("readyState" in fn or "textLength" in fn):
                return {"title": "", "url": "about:blank", "ready": "complete", "textLength": 0, "heading": ""}
            if "textLength" in fn:
                return {
                    "title": "Example Domain",
                    "url": "https://example.com/",
                    "ready": "complete",
                    "textLength": 80,
                    "heading": "",
                }
            if "outerHTML" in fn:
                return "<html><head><title>Example Domain</title></head><body><h1>Example Domain</h1></body></html>"
            if "parse_error" in fn:
                return {"count": 0, "blocks": [], "paths": []}
            if "scrollTo" in fn:
                return {"height": 400, "url": "https://example.com/", "title": "Example Domain"}
            return {
                "title": {
                    "value": "Example Domain",
                    "exact_verified": True,
                    "selector": "h1",
                    "selector_candidates": ["h1"],
                    "match_count": 1,
                    "selector_unique": True,
                }
            }
        raise AssertionError(name)


class FakeLLM:
    def __init__(self) -> None:
        self.calls = 0
        self.tool_names: list[str] = []
        self.snapshot = ""

    async def chat_with_tools(self, system, user, tools, executor, max_rounds, max_tool_output_chars, max_repeated_tool_calls, max_tool_calls=None, round_delay_seconds=0.0, on_event=None):
        self.calls += 1
        self.tool_names = [tool["function"]["name"] for tool in tools]
        self.snapshot = await executor("browser_snapshot", {})
        if on_event:
            await on_event("tool_call", {"round": 1, "name": "browser_snapshot", "arguments": {}})
            await on_event("tool_result", {"round": 1, "name": "browser_snapshot"})
            await on_event("final_response", {"round": 1})
        return {
            "page_title": "",
            "anti_bot_state": "NONE",
            "fields": [{
                "path": "title",
                "found": True,
                "value": "Example Domain",
                "selector": "h1",
                "selector_type": "css",
                "selector_candidates": ["h1"],
                "confidence": 0.99,
            }],
            "proposed_json": {"title": "Example Domain"},
            "evidence": ["snapshot title"],
            "crawler_notes": [],
        }

    async def chat_json(self, system, user, max_tokens=None):
        raise AssertionError("analysis must stay on the browser tool path")


def test_subprocess_env_forwards_display_and_drops_secrets():
    env = build_mcp_subprocess_env({
        "HOME": "/home/ubuntu",
        "PATH": "/usr/bin",
        "USER": "ubuntu",
        "DISPLAY": ":1",
        "XAUTHORITY": "/home/ubuntu/.Xauthority",
        "PLAYWRIGHT_MCP_HEADLESS": "false",
        "PLAYWRIGHT_MCP_BROWSER": "chromium",
        "LLM_API_KEY": "sk-secret",
        "OPENAI_API_KEY": "sk-other",
        "PLAYWRIGHT_MCP_API_KEY": "sk-secret",
    })
    assert env["DISPLAY"] == ":1"
    assert env["XAUTHORITY"] == "/home/ubuntu/.Xauthority"
    assert env["PLAYWRIGHT_MCP_HEADLESS"] == "false"
    assert env["PLAYWRIGHT_MCP_BROWSER"] == "chromium"
    assert "LLM_API_KEY" not in env
    assert "OPENAI_API_KEY" not in env
    assert "PLAYWRIGHT_MCP_API_KEY" not in env


def test_headless_resolution_matches_playwright_mcp_rules():
    visible = build_mcp_subprocess_env({"HOME": "/h", "PATH": "/usr/bin", "USER": "u", "DISPLAY": ":1"})
    headed = describe_browser_launch(["@playwright/mcp@latest", "--user-data-dir", ".browser-profile"], visible, "Linux")
    assert headed["args_headless"] is False
    assert headed["playwright_mcp_headless"] is None
    assert headed["effective_headless"] is False
    assert headed["reason"] == "headed_default"

    hidden = build_mcp_subprocess_env({"HOME": "/h", "PATH": "/usr/bin", "USER": "u"})
    forced = describe_browser_launch(["@playwright/mcp@latest"], hidden, "Linux")
    assert forced["effective_headless"] is True
    assert forced["reason"] == "linux_without_display"

    flagged = describe_browser_launch(["@playwright/mcp@latest", "--headless"], visible, "Linux")
    assert flagged["effective_headless"] is True
    assert flagged["args_headless"] is True

    env_forced = build_mcp_subprocess_env({
        "HOME": "/h",
        "PATH": "/usr/bin",
        "USER": "u",
        "DISPLAY": ":1",
        "PLAYWRIGHT_MCP_HEADLESS": "true",
    })
    assert describe_browser_launch(["@playwright/mcp@latest"], env_forced, "Linux")["effective_headless"] is True


def test_tool_error_is_not_a_successful_payload():
    with pytest.raises(MCPToolError, match="Executable doesn't exist"):
        interpret_call_result("browser_navigate", CallResult("### Error\nExecutable doesn't exist\n", is_error=True))


def test_evaluate_result_and_html_are_unwrapped():
    value = interpret_call_result(
        "browser_evaluate",
        CallResult('### Result\n{\n  "title": "Example Domain",\n  "url": "https://example.com/"\n}\n'),
    )
    assert value == {"title": "Example Domain", "url": "https://example.com/"}
    html = '### Result\n"<html><title>Example Domain</title></html>"\n'
    assert unwrap_mcp_payload(html) == "<html><title>Example Domain</title></html>"
    assert ExtractorAgent._coerce_source(html) == "<html><title>Example Domain</title></html>"


def test_agent_receives_browser_navigate_and_snapshot_is_not_navigation():
    browser = ScriptedBrowser()
    agent = ExtractorAgent()
    agent.browser = browser
    llm = FakeLLM()
    agent.llm = llm

    async def tool_names():
        defs = await agent._get_browser_tool_defs()
        return [item["function"]["name"] for item in defs]

    names = asyncio.run(tool_names())
    assert names[:3] == ["browser_navigate", "browser_snapshot", "browser_evaluate"]
    assert "browser_close" not in names
    assert "browser_navigate_back" not in names
    assert ExtractorAgent._tool_is_allowed("browser_navigate")
    assert not ExtractorAgent._tool_is_allowed("browser_navigate_back")
    assert set(AGENT_BROWSER_TOOLS) >= {"browser_navigate", "browser_snapshot", "browser_evaluate"}


def test_navigation_failure_is_not_an_empty_extraction(caplog):
    agent = ExtractorAgent()
    agent.browser = ScriptedBrowser(fail_navigation=True)
    agent.llm = FakeLLM()
    with caplog.at_level(logging.INFO, logger="crawler.mcp"):
        with pytest.raises(RuntimeError, match="Browser navigation failed"):
            asyncio.run(agent.inspect("https://example.com", {"title": ""}))
    assert agent.llm.calls == 0
    assert "browser_snapshot" not in [name for name, _ in agent.browser.calls]
    assert "MCP_ERROR" in caplog.text
    assert "EXTRACTION_RESULT" in caplog.text
    assert '"status": "FAILED"' in caplog.text


def test_blank_page_is_not_a_successful_empty_result():
    agent = ExtractorAgent()
    agent.browser = ScriptedBrowser(blank=True)
    agent.llm = FakeLLM()
    with pytest.raises(RuntimeError, match="did not open the requested page"):
        asyncio.run(agent.inspect("https://example.com", {"title": ""}))
    assert agent.llm.calls == 0


def test_confirmed_page_uses_browser_title_and_calls_navigate_before_snapshot():
    browser = ScriptedBrowser()
    llm = FakeLLM()
    agent = ExtractorAgent()
    agent.browser = browser
    agent.llm = llm
    result = asyncio.run(agent.inspect("https://example.com", {"title": ""}))
    names = [name for name, _ in browser.calls]
    assert names.index("browser_navigate") < names.index("browser_snapshot")
    assert result.page_title == "Example Domain"
    assert result.proposed_json["title"] == "Example Domain"
    assert result.status == "READY"
    assert "browser_navigate" in llm.tool_names
    assert "Example Domain" in str(llm.snapshot)
    assert "Example Domain" in agent.page_state["snapshot"]
    assert agent.page_state["url"] == "https://example.com/"
