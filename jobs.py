"""Make plans in the background so the page can show what's happening (issue #15).

A plan takes about a minute. Instead of one long request, the page starts a
job, asks for its latest progress message every second, and loads the
result when the job is done. Jobs live in memory: this is a small app run on
one laptop, and nothing about a plan is saved.
"""

import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import progress as stages

logger = logging.getLogger(__name__)

KEEP_SECONDS = 15 * 60  # long enough to reload the result page, short enough not to pile up

RUNNING, DONE = "running", "done"


@dataclass
class Job:
    id: str
    state: str = RUNNING
    message: str = stages.STARTING
    page: dict = field(default_factory=dict)  # what the result page is rendered with
    status_code: int = 200
    started: float = field(default_factory=time.monotonic)
    finished: float | None = None  # set when the job is done


# The work for one plan: given a way to report progress, returns (page context, HTTP status).
Work = Callable[[Callable[[str], None]], tuple[dict, int]]


class Jobs:
    def __init__(self, keep_seconds: float = KEEP_SECONDS, run_in_background: bool = True):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self.keep_seconds = keep_seconds
        self.run_in_background = run_in_background  # tests run jobs straight away

    def start(self, work: Work, fallback_page: dict) -> Job:
        """Start `work` and return its job. If it crashes, the job ends with `fallback_page` and a 500."""
        self._forget_old()
        job = Job(id=secrets.token_urlsafe(16))
        with self._lock:
            self._jobs[job.id] = job

        def report(message: str) -> None:
            job.message = message

        def run() -> None:
            try:
                page, code = work(report)
            except Exception:
                logger.exception("Plan job %s failed", job.id)
                page, code = {**fallback_page, "error": "Something went wrong making the plan. Please try again."}, 500
            job.page, job.status_code = page, code
            job.finished = time.monotonic()
            job.message, job.state = stages.DONE, DONE

        if self.run_in_background:
            threading.Thread(target=run, name=f"plan-{job.id[:6]}", daemon=True).start()
        else:
            run()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _forget_old(self) -> None:
        """Forget jobs that finished more than `keep_seconds` ago. Running jobs are always kept."""
        cutoff = time.monotonic() - self.keep_seconds
        with self._lock:
            old = [j.id for j in self._jobs.values() if j.state == DONE and j.finished is not None and j.finished < cutoff]
            for job_id in old:
                del self._jobs[job_id]
