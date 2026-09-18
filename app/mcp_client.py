from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any

from .settings import settings


class PlaywrightMCP:
    """Thin runtime adapter around the Python MCP client.

    MCP is imported lazily so static Requests/BS4 analysis can run even on a machine
    where the browser extra has not been installed yet.
    """

    def __init__(self) -> None:
        self.stack = AsyncExitStack()
        self.session = None

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

        if settings.mcp_mode.lower() == "http":
            streams = await self.stack.enter_async_context(
                streamable_http_client(settings.mcp_url)
            )
            if len(streams) == 2:
                read_stream, write_stream = streams
            elif len(streams) == 3:
                read_stream, write_stream, _ = streams
            else:
                raise RuntimeError(f"Unexpected MCP transport shape: {len(streams)} values")
        else:
            params = StdioServerParameters(
                command=settings.mcp_command,
                args=list(settings.mcp_args),
            )
            read_stream, write_stream = await self.stack.enter_async_context(stdio_client(params))

        self.session = await self.stack.enter_async_context(ClientSession(read_stream, write_stream))
        await self.session.initialize()

    async def list_tools(self) -> list[Any]:
        await self.connect()
        result = await self.session.list_tools()
        return list(getattr(result, "tools", []) or [])

    async def close(self) -> None:
        await self.stack.aclose()
        self.session = None
        self.stack = AsyncExitStack()

    async def call(self, name: str, args: dict[str, Any] | None = None) -> Any:
        await self.connect()
        result = await self.session.call_tool(name, arguments=args or {})
        structured = getattr(result, "structured_content", None)
        if structured is not None:
            return structured
        content = getattr(result, "content", None)
        if content is None:
            return result
        parts=[]
        for item in content:
            text=getattr(item,"text",None)
            parts.append(text if text is not None else str(item))
        return "\n".join(parts)
