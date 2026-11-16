from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AnalysisRun:
    id: str
    status: str = "QUEUED"
    stage: str = "queued"
    events: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] | None = None
    job_id: str | None = None
    error: str | None = None
    created_at: str = field(default_factory=now_iso)
    started_at: str | None = None
    finished_at: str | None = None

    def emit(self, stage: str, message: str, level: str = "info", meta: dict[str, Any] | None = None) -> None:
        self.stage = stage
        self.events.append({
            "ts": now_iso(),
            "stage": stage,
            "message": message,
            "level": level,
            "meta": meta or {},
        })

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


class AnalysisRunStore:
    def __init__(self) -> None:
        self._runs: dict[str, AnalysisRun] = {}
        self._lock = asyncio.Lock()

    async def create(self, run_id: str) -> AnalysisRun:
        async with self._lock:
            run = AnalysisRun(run_id)
            self._runs[run_id] = run
            return run

    async def get(self, run_id: str) -> AnalysisRun | None:
        async with self._lock:
            return self._runs.get(run_id)

    async def emit(self, run_id: str, stage: str, message: str, level: str = "info", meta: dict[str, Any] | None = None) -> None:
        async with self._lock:
            run = self._runs.get(run_id)
            if run:
                run.emit(stage, message, level, meta)

    async def update(self, run_id: str, **kwargs: Any) -> None:
        async with self._lock:
            run = self._runs.get(run_id)
            if run:
                for key, value in kwargs.items():
                    setattr(run, key, value)

    async def snapshot(self, run_id: str) -> dict[str, Any] | None:
        async with self._lock:
            run = self._runs.get(run_id)
            return run.snapshot() if run else None
