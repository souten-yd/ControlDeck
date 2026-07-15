from __future__ import annotations

from typing import Protocol

from app.jobs.service import Job


class CodeAgentProvider(Protocol):
    async def run(
        self, job: Job, *, operation: str, project_path: str, instruction: str,
        base_url: str = "", model: str = "",
    ) -> dict: ...
