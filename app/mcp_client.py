from __future__ import annotations

import asyncio
import sys
from contextlib import AsyncExitStack
from typing import Any

from .mcp_launch import build_mcp_stdio_command
from .settings import settings


class PlaywrightMCP:
    """Thin runtime adapter around the Python MCP client.

    MCP is imported lazily so the application can still start when browser support
    has not been installed; Playwright is the only page acquisition runtime.
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
            command = settings.mcp_command
            launch_args = list(settings.mcp_args)
            # On Windows, npx.cmd lets cmd.exe write its copyright banner onto
            # stdout. Resolving node.exe + the package bin can install the
            # package, so keep that work off the event loop.
            if sys.platform == "win32":
                command, launch_args = await asyncio.to_thread(
                    build_mcp_stdio_command,
                    command,
                    launch_args,
                )
            else:
                command, launch_args = build_mcp_stdio_command(command, launch_args)
            params = StdioServerParameters(command=command, args=launch_args)
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
