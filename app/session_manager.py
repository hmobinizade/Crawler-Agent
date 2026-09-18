from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .extractor import ExtractorAgent


@dataclass
class BrowserSession:
    id: str
    url: str
    template: dict[str, Any]
    agent: ExtractorAgent


class BrowserSessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, BrowserSession] = {}

    async def create(self, session_id: str, url: str, template: dict[str, Any]) -> BrowserSession:
        agent = ExtractorAgent()
        await agent.open_page(url)
        session = BrowserSession(session_id, url, template, agent)
        self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> BrowserSession:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise KeyError(f"Unknown browser session: {session_id}") from exc

    async def analyze(self, session_id: str):
        session = self.get(session_id)
        result = await session.agent.inspect(session.url, session.template, navigate=False)
        return result, dict(session.agent.last_artifacts)

    async def close(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session:
            await session.agent.browser.close()

    async def close_all(self) -> None:
        for session_id in list(self._sessions):
            await self.close(session_id)
