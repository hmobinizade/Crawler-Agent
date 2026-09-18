from __future__ import annotations

from .models import Job


class InMemoryStore:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}

    def put(self, job: Job) -> None:
        self.jobs[job.id] = job

    def get(self, job_id: str) -> Job:
        return self.jobs[job_id]


store = InMemoryStore()
