from __future__ import annotations

import json
import logging
import os
import platform
import re
import sys
import time
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .settings import settings

log = logging.getLogger("crawler.mcp")
if not log.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    log.addHandler(_handler)
    log.setLevel(logging.INFO)
    log.propagate = False

_DESKTOP_ENV = (
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "XDG_RUNTIME_DIR",
    "XDG_CURRENT_DESKTOP",
    "XDG_SESSION_TYPE",
    "XDG_SESSION_ID",
    "DBUS_SESSION_BUS_ADDRESS",
    "SESSIONNAME",
    "CLIENTNAME",
)
_BASE_ENV_UNIX = ("HOME", "LOGNAME", "PATH", "SHELL", "TERM", "USER")
_BASE_ENV_WINDOWS = (
    "APPDATA",
    "HOMEDRIVE",
    "HOMEPATH",
    "LOCALAPPDATA",
    "PATH",
    "PATHEXT",
    "PROCESSOR_ARCHITECTURE",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "USERNAME",
    "USERPROFILE",
)
_SECRET_MARKERS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD", "CREDENTIAL", "COOKIE", "AUTHORIZATION")
_SENSITIVE_QUERY = {"access_token", "token", "key", "secret", "password", "auth", "code", "session", "signature"}
_SECTION_RE = re.compile(r"^### (.+)$", re.M)
_PAGE_URL_RE = re.compile(r"^-\s*Page URL:\s*(.+)$", re.M)
_PAGE_TITLE_RE = re.compile(r"^-\s*Page Title:\s*(.*)$", re.M)


class MCPToolError(RuntimeError):
    def __init__(self, tool: str, detail: str) -> None:
        self.tool = tool
        self.detail = detail
        super().__init__(f"{tool} failed: {detail}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _secret_name(name: str) -> bool:
    upper = name.upper()
    return any(marker in upper for marker in _SECRET_MARKERS)


def redact_url(value: str) -> str:
    try:
        parts = urlsplit(value)
    except Exception:
        return value[:300]
    if not parts.scheme or not parts.query:
        return value[:500]
    query = []
    for key, item in parse_qsl(parts.query, keep_blank_values=True):
        query.append((key, "<redacted>" if key.lower() in _SENSITIVE_QUERY else item))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))[:500]


def redact_tool_args(args: Mapping[str, Any] | None) -> dict[str, Any]:
    if not args:
        return {}
    redacted: dict[str, Any] = {}
    for key, value in args.items():
        if _secret_name(str(key)):
            redacted[str(key)] = "<redacted>"
            continue
        if isinstance(value, str) and str(key).lower() in {"function", "code", "script", "js"} and len(value) > 180:
            redacted[str(key)] = f"<js {len(value)} chars>"
        elif isinstance(value, str) and str(key).lower() in {"url", "uri", "href"}:
            redacted[str(key)] = redact_url(value)
        elif isinstance(value, str):
            redacted[str(key)] = value[:300]
        else:
            redacted[str(key)] = value
    return redacted


def build_mcp_subprocess_env(parent: Mapping[str, str] | None = None) -> dict[str, str]:
    """Environment for the Playwright MCP child.

    The Python MCP stdio client keeps only a small safe set and drops DISPLAY.
    Playwright MCP then forces headless mode on Linux when DISPLAY is absent,
    so a headed browser never appears on the parent's desktop. Forward the
    desktop session and Playwright settings. Do not forward API keys.
    """
    source = os.environ if parent is None else parent
    env: dict[str, str] = {}
    base = _BASE_ENV_WINDOWS if platform.system() == "Windows" else _BASE_ENV_UNIX
    for key in (*base, *_DESKTOP_ENV):
        value = source.get(key)
        if value:
            env[key] = value
    for key, value in source.items():
        if key.startswith("PLAYWRIGHT_MCP_") or key.startswith("PLAYWRIGHT_"):
            if _secret_name(key):
                continue
            env[key] = value
    return env


def describe_browser_launch(args: list[str], env: Mapping[str, str], system: str | None = None) -> dict[str, Any]:
    """Mirror @playwright/mcp headless resolution for diagnostics."""
    system_name = system or platform.system()
    raw = env.get("PLAYWRIGHT_MCP_HEADLESS")
    env_headless: bool | None
    if raw is None or str(raw).strip() == "":
        env_headless = None
    else:
        env_headless = str(raw).strip().lower() in {"1", "true", "yes", "on"}
    args_headless = "--headless" in args
    display = bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))
    if args_headless or env_headless is True:
        effective = True
        reason = "--headless" if args_headless else "PLAYWRIGHT_MCP_HEADLESS"
    elif env_headless is False:
        effective = False
        reason = "PLAYWRIGHT_MCP_HEADLESS=false"
    elif system_name == "Linux" and not display:
        effective = True
        reason = "linux_without_display"
    else:
        effective = False
        reason = "headed_default"
    return {
        "args_headless": args_headless,
        "playwright_mcp_headless": None if raw is None or str(raw).strip() == "" else str(raw).strip(),
        "display_present": display,
        "effective_headless": effective,
        "reason": reason,
    }


def describe_runtime() -> dict[str, Any]:
    system_name = platform.system()
    info: dict[str, Any] = {
        "system": system_name,
        "pid": os.getpid(),
        "python": platform.python_version(),
    }
    if system_name == "Windows":
        session_id = _windows_session_id(os.getpid())
        info["windows_session_id"] = session_id
        info["interactive_desktop"] = session_id not in (None, 0)
        info["child_session"] = "inherited_from_parent"
    else:
        info["windows_session_id"] = None
        info["display"] = os.environ.get("DISPLAY")
        info["wayland"] = os.environ.get("WAYLAND_DISPLAY")
        info["interactive_desktop"] = bool(info["display"] or info["wayland"])
        info["child_session"] = "inherited_from_parent"
    return info


def _windows_session_id(pid: int) -> int | None:
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        session_id = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.ProcessIdToSessionId(ctypes.c_ulong(pid), ctypes.byref(session_id))
        if not ok:
            return None
        return int(session_id.value)
    except Exception:
        return None


def resolve_mcp_launch() -> dict[str, Any]:
    mode = os.getenv("MCP_MODE", settings.mcp_mode) or settings.mcp_mode
    command = os.getenv("MCP_COMMAND", settings.mcp_command) or settings.mcp_command
    raw_args = os.getenv("MCP_ARGS")
    if raw_args:
        parsed = json.loads(raw_args)
        if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
            raise RuntimeError("MCP_ARGS must be a JSON list of strings")
        args = parsed
    else:
        args = list(settings.mcp_args)
    return {"mode": mode, "command": command, "args": args, "url": os.getenv("MCP_URL", settings.mcp_url)}


def split_mcp_sections(text: str) -> dict[str, str]:
    matches = list(_SECTION_RE.finditer(text))
    if not matches:
        return {}
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[match.group(1).strip()] = text[start:end].strip()
    return sections


def _strip_fence(body: str) -> str:
    lines = body.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def parse_result_body(body: str) -> Any:
    text = _strip_fence(body.strip())
    if not text:
        return ""
    try:
        return json.loads(text)
    except Exception:
        return text


def unwrap_mcp_payload(payload: Any) -> Any:
    if not isinstance(payload, str) or not payload.lstrip().startswith("###"):
        return payload
    sections = split_mcp_sections(payload)
    if "Result" not in sections:
        return payload
    return parse_result_body(sections["Result"])


def page_meta_from_tool_output(payload: Any) -> dict[str, str]:
    if isinstance(payload, dict):
        return {
            "url": str(payload.get("url") or payload.get("page_url") or ""),
            "title": str(payload.get("title") or payload.get("page_title") or ""),
        }
    text = str(payload or "")
    url_match = _PAGE_URL_RE.search(text)
    title_match = _PAGE_TITLE_RE.search(text)
    return {
        "url": (url_match.group(1).strip() if url_match else ""),
        "title": (title_match.group(1).strip() if title_match else ""),
    }


def snapshot_summary(payload: Any) -> dict[str, Any]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return {
        "chars": len(text),
        "lines": len(lines),
        "preview": " | ".join(lines[:8])[:400],
    }


def tool_result_text(result: Any) -> str:
    content = getattr(result, "content", None)
    if content is None:
        return str(result)
    parts: list[str] = []
    for item in content:
        text = getattr(item, "text", None)
        parts.append(text if text is not None else str(item))
    return "\n".join(parts)


def interpret_call_result(tool: str, result: Any) -> Any:
    if getattr(result, "isError", False):
        detail = tool_result_text(result).strip() or "tool returned isError"
        raise MCPToolError(tool, detail[:1200])
    structured = getattr(result, "structuredContent", None)
    if structured is None:
        structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict) and structured:
        return structured
    text = tool_result_text(result)
    sections = split_mcp_sections(text)
    if "Error" in sections and "Result" not in sections:
        raise MCPToolError(tool, sections["Error"][:1200])
    if "Result" in sections:
        return parse_result_body(sections["Result"])
    return text


def emit(event: str, payload: Mapping[str, Any]) -> None:
    safe = {key: value for key, value in payload.items() if not _secret_name(str(key))}
    log.info("%s %s", event, json.dumps(safe, ensure_ascii=False, default=str))


class PlaywrightMCP:
    """Thin runtime adapter around the Python MCP client.

    MCP is imported lazily so the application can still start when browser support
    has not been installed. Playwright MCP is the only page-acquisition runtime.
    The stdio session stays open from connect() until close().
    """

    def __init__(self) -> None:
        self.stack = AsyncExitStack()
        self.session = None
        self.current_url = ""
        self.current_title = ""
        self.launch: dict[str, Any] = {}
        self._connected_at: float | None = None
        self._events: list[dict[str, Any]] = []

    def _record(self, event: str, payload: dict[str, Any]) -> None:
        item = {"event": event, "at": _now(), **payload}
        self._events.append(item)
        emit(event, item)

    async def connect(self) -> None:
        if self.session is not None:
            return
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            from mcp.client.streamable_http import streamable_http_client
        except ImportError as exc:
            raise RuntimeError(
                "MCP browser support is not installed. Install the 'mcp' package before using Playwright analysis."
            ) from exc

        launch = resolve_mcp_launch()
        child_env = build_mcp_subprocess_env()
        browser = describe_browser_launch(launch["args"], child_env)
        runtime = describe_runtime()
        self.launch = {**launch, "browser": browser, "runtime": runtime}
        self._record(
            "MCP_START",
            {
                "phase": "spawn",
                "mode": launch["mode"],
                "command": launch["command"],
                "args": launch["args"],
                "session_alive": False,
                "headless": browser,
                "runtime": {
                    "system": runtime.get("system"),
                    "pid": runtime.get("pid"),
                    "windows_session_id": runtime.get("windows_session_id"),
                    "interactive_desktop": runtime.get("interactive_desktop"),
                    "child_session": runtime.get("child_session"),
                    "display_forwarded": "DISPLAY" in child_env or "WAYLAND_DISPLAY" in child_env,
                },
            },
        )
        started = time.perf_counter()
        try:
            if launch["mode"].lower() == "http":
                streams = await self.stack.enter_async_context(streamable_http_client(launch["url"]))
                if len(streams) == 2:
                    read_stream, write_stream = streams
                elif len(streams) == 3:
                    read_stream, write_stream, _ = streams
                else:
                    raise RuntimeError(f"Unexpected MCP transport shape: {len(streams)} values")
            else:
                params = StdioServerParameters(
                    command=launch["command"],
                    args=list(launch["args"]),
                    env=child_env,
                )
                read_stream, write_stream = await self.stack.enter_async_context(stdio_client(params))
            self.session = await self.stack.enter_async_context(ClientSession(read_stream, write_stream))
            await self.session.initialize()
        except Exception as exc:
            self._record("MCP_ERROR", {"phase": "connect", "error": str(exc)[:800]})
            await self.stack.aclose()
            self.stack = AsyncExitStack()
            self.session = None
            raise
        self._connected_at = time.perf_counter()
        self._record(
            "MCP_START",
            {
                "phase": "ready",
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "session_alive": True,
            },
        )

    async def list_tools(self) -> list[Any]:
        await self.connect()
        started = time.perf_counter()
        try:
            result = await self.session.list_tools()
        except Exception as exc:
            self._record("MCP_ERROR", {"phase": "tools_list", "error": str(exc)[:800], "session_alive": self.session is not None})
            raise
        tools = list(getattr(result, "tools", []) or [])
        names = [str(getattr(tool, "name", "")) for tool in tools if getattr(tool, "name", "")]
        self._record(
            "MCP_TOOLS",
            {
                "tools": names,
                "count": len(names),
                "has_browser_navigate": "browser_navigate" in names,
                "has_browser_snapshot": "browser_snapshot" in names,
                "has_browser_evaluate": "browser_evaluate" in names,
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "session_alive": self.session is not None,
            },
        )
        return tools

    async def close(self) -> None:
        alive = self.session is not None
        started = time.perf_counter()
        try:
            await self.stack.aclose()
        finally:
            self.session = None
            self.stack = AsyncExitStack()
            if alive or self._connected_at is not None:
                self._record(
                    "MCP_START",
                    {
                        "phase": "closed",
                        "session_alive": False,
                        "elapsed_ms": int((time.perf_counter() - started) * 1000),
                    },
                )
            self._connected_at = None

    def _event_for(self, name: str) -> str:
        return {
            "browser_navigate": "BROWSER_NAVIGATE",
            "browser_snapshot": "BROWSER_SNAPSHOT",
            "browser_evaluate": "BROWSER_EVALUATE",
        }.get(name, "MCP_TOOL")

    def _remember_page(self, raw_text: str, value: Any) -> None:
        meta = page_meta_from_tool_output(raw_text)
        parsed = page_meta_from_tool_output(value)
        url = parsed.get("url") or meta.get("url") or ""
        title = parsed.get("title") or meta.get("title") or ""
        if url:
            self.current_url = url
        if title:
            self.current_title = title

    async def call(self, name: str, args: dict[str, Any] | None = None) -> Any:
        await self.connect()
        event = self._event_for(name)
        started = time.perf_counter()
        safe_args = redact_tool_args(args)
        self._record(
            event,
            {
                "tool": name,
                "phase": "start",
                "arguments": safe_args,
                "session_alive": self.session is not None,
                "current_url": self.current_url,
                "current_title": self.current_title,
            },
        )
        try:
            result = await self.session.call_tool(name, arguments=args or {})
            raw_text = tool_result_text(result)
            value = interpret_call_result(name, result)
        except Exception as exc:
            self._record(
                "MCP_ERROR",
                {
                    "tool": name,
                    "phase": "error",
                    "arguments": safe_args,
                    "error": str(exc)[:800],
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                    "session_alive": self.session is not None,
                    "current_url": self.current_url,
                    "current_title": self.current_title,
                },
            )
            raise
        self._remember_page(raw_text, value)
        summary = snapshot_summary(raw_text if name == "browser_snapshot" else value)
        self._record(
            event,
            {
                "tool": name,
                "phase": "end",
                "arguments": safe_args,
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "session_alive": self.session is not None,
                "current_url": self.current_url,
                "current_title": self.current_title,
                "result": summary,
            },
        )
        return value
