"""Real Playwright MCP cycle. Skipped unless RUN_LIVE_BROWSER=1.

The test uses MCP_MODE=stdio and MCP_COMMAND=npx. It does not fall back to
requests or an HTML parser.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import pytest

pytestmark = pytest.mark.skipif(os.getenv("RUN_LIVE_BROWSER") != "1", reason="set RUN_LIVE_BROWSER=1 to open a real browser")

EXAMPLE_URL = "https://example.com"
PROJECT_URL = "https://www.tabnak.ir/fa/news/1393602/"


def _descendants(pid: int) -> list[tuple[int, str, str]]:
    by_parent: dict[int, list[tuple[int, str]]] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
            end = stat.rfind(")")
            comm = stat[stat.find("(") + 1 : end]
            ppid = int(stat[end + 2 :].split()[1])
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
        except Exception:
            continue
        by_parent.setdefault(ppid, []).append((int(entry.name), comm, cmdline))
    found: list[tuple[int, str, str]] = []
    stack = [pid]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        for child, comm, cmdline in by_parent.get(current, []):
            if child in seen:
                continue
            seen.add(child)
            found.append((child, comm, cmdline))
            stack.append(child)
    return found


def _child_env(pid: int) -> dict[str, str]:
    raw = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    env: dict[str, str] = {}
    for item in raw:
        if b"=" not in item:
            continue
        key, value = item.split(b"=", 1)
        env[key.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    return env


def test_live_example_and_project_page():
    report = asyncio.run(_run())
    Path("/tmp/live_browser_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    assert report["example"]["title"] == "Example Domain"
    assert "Example Domain" in report["example"]["snapshot"]
    assert report["example"]["navigation_called"] is True
    assert report["session_alive_after_tools"] is True
    assert report["session_alive_after_example"] is True
    assert report["display_forwarded"] is True
    assert report["effective_headless"] is False
    assert report["chrome_headless_flag"] is False
    assert report["window_visible"]["visible"] is True
    assert report["project"]["host_ok"] is True
    assert report["project"]["snapshot_chars"] > 40
    assert report["project"]["title"]
    assert report["closed"] is True


async def _run() -> dict:
    from app.mcp_client import PlaywrightMCP

    browser = PlaywrightMCP()
    report: dict = {"errors": []}
    try:
        await browser.connect()
        session_id = id(browser.session)
        tools = await browser.list_tools()
        names = [getattr(tool, "name", "") for tool in tools]
        report["tools"] = names
        report["session_alive_after_tools"] = browser.session is not None and id(browser.session) == session_id
        report["launch"] = {
            "mode": browser.launch.get("mode"),
            "command": browser.launch.get("command"),
            "args": browser.launch.get("args"),
            "browser": browser.launch.get("browser"),
            "runtime": browser.launch.get("runtime"),
        }
        node = next((item for item in _descendants(os.getpid()) if "playwright-mcp" in item[2] or item[1] == "node"), None)
        child_env = _child_env(node[0]) if node else {}
        report["mcp_pid"] = node[0] if node else None
        report["display_forwarded"] = bool(child_env.get("DISPLAY") or child_env.get("WAYLAND_DISPLAY"))
        report["child_playwright_mcp_headless"] = child_env.get("PLAYWRIGHT_MCP_HEADLESS")
        report["effective_headless"] = bool((browser.launch.get("browser") or {}).get("effective_headless"))
        nav = await browser.call("browser_navigate", {"url": EXAMPLE_URL})
        snapshot = await browser.call("browser_snapshot", {})
        evaluated = await browser.call(
            "browser_evaluate",
            {"function": "() => ({title: document.title, url: location.href, ready: document.readyState, text: (document.body && document.body.innerText || '').slice(0, 240)})"},
        )
        chrome = [item for item in _descendants(os.getpid()) if "chrome" in item[1] or "chrom" in item[2].lower()]
        headless_flag = any("--headless" in item[2] for item in chrome)
        window_visible = _window_visible()
        for _ in range(10):
            if window_visible.get("visible"):
                break
            await asyncio.sleep(0.3)
            window_visible = _window_visible()
        report["example"] = {
            "navigation_called": True,
            "navigate_excerpt": str(nav)[:500],
            "snapshot": str(snapshot)[:2000],
            "title": evaluated.get("title") if isinstance(evaluated, dict) else "",
            "url": evaluated.get("url") if isinstance(evaluated, dict) else "",
            "ready": evaluated.get("ready") if isinstance(evaluated, dict) else "",
            "text": evaluated.get("text") if isinstance(evaluated, dict) else "",
        }
        report["chrome_count"] = len(chrome)
        report["chrome_headless_flag"] = headless_flag
        report["window_visible"] = window_visible
        report["session_alive_after_example"] = browser.session is not None and id(browser.session) == session_id

        project_nav = await browser.call("browser_navigate", {"url": PROJECT_URL})
        project_snapshot = await browser.call("browser_snapshot", {})
        project_state = await browser.call(
            "browser_evaluate",
            {"function": "() => ({title: document.title, url: location.href, ready: document.readyState, textLength: ((document.body && document.body.innerText) || '').trim().length})"},
        )
        project_url = project_state.get("url", "") if isinstance(project_state, dict) else ""
        report["project"] = {
            "requested": PROJECT_URL,
            "url": project_url,
            "title": project_state.get("title", "") if isinstance(project_state, dict) else "",
            "ready": project_state.get("ready", "") if isinstance(project_state, dict) else "",
            "text_length": project_state.get("textLength", 0) if isinstance(project_state, dict) else 0,
            "snapshot_chars": len(str(project_snapshot)),
            "snapshot_excerpt": str(project_snapshot)[:800],
            "navigate_excerpt": str(project_nav)[:300],
            "host_ok": urlparse(project_url).netloc.lower().endswith("tabnak.ir"),
        }
        report["session_alive_after_project"] = browser.session is not None and id(browser.session) == session_id
    except Exception as exc:
        report["errors"].append(f"{type(exc).__name__}: {exc}")
        raise
    finally:
        await browser.close()
        report["closed"] = browser.session is None
        Path("/tmp/live_browser_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _window_visible() -> dict:
    import shutil
    import subprocess

    if not shutil.which("xwininfo"):
        return {"visible": False, "reason": "xwininfo missing", "matches": []}
    try:
        tree = subprocess.check_output(["xwininfo", "-root", "-tree"], text=True, stderr=subprocess.DEVNULL, timeout=5)
    except Exception as exc:
        return {"visible": False, "reason": str(exc), "matches": []}
    matches = []
    for line in tree.splitlines():
        lowered = line.lower()
        if "example domain" in lowered or "chrome" in lowered or "chromium" in lowered:
            matches.append(line.strip()[:240])
    return {"visible": bool(matches), "matches": matches[:12]}
