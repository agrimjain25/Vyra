"""Background jobs: persisted rows + in-process asyncio tasks. Duplicate active jobs are refused by the DB."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from ..logging_setup import log
from ..providers.hub import GenerationFailed
from ..repo import Repo

logger = logging.getLogger("vyra.jobs")
JobFn = Callable[[Callable[..., None]], Awaitable[dict | None]]


class JobManager:
    def __init__(self, repo: Repo):
        self.repo = repo
        self.tasks: dict[str, asyncio.Task] = {}

    def submit(self, kind: str, target_key: str, fn: JobFn, *, campaign_id: str | None = None,
               experience_id: str | None = None) -> tuple[dict, bool]:
        """Start ``fn`` unless an identical job is already running. Returns (job, started)."""
        job, created = self.repo.create_job(kind, target_key, campaign_id, experience_id)
        if not created:
            return job, False
        self.tasks[job["id"]] = asyncio.create_task(self._run(job["id"], kind, fn))
        return job, True

    async def _run(self, jid: str, kind: str, fn: JobFn) -> None:
        self.repo.update_job(jid, status="running")

        def progress(**p) -> None:
            cur = (self.repo.get_job(jid) or {}).get("progress", {})
            cur.update(p)
            self.repo.update_job(jid, progress=cur)

        try:
            result = await fn(progress)
            self.repo.update_job(jid, status="succeeded", result=result or {})
        except GenerationFailed as exc:
            self.repo.update_job(jid, status="failed", error=exc.as_dict())
        except asyncio.CancelledError:
            self.repo.update_job(jid, status="failed", error={"code": "cancelled", "message": "Cancelled."})
            raise
        except Exception as exc:  # noqa: BLE001 - last-resort guard so a job never stays "running"
            log(logger, logging.ERROR, "job crashed", job=jid, kind=kind, error=type(exc).__name__)
            logger.exception("job crashed")
            self.repo.update_job(jid, status="failed",
                                 error={"code": "internal_error", "message": "Something went wrong on the server."})
        finally:
            self.tasks.pop(jid, None)

    async def shutdown(self) -> None:
        for t in list(self.tasks.values()):
            t.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
