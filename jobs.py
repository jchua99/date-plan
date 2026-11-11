"""Make plans in the background so the page can show what's happening (issue #15).

A plan takes about a minute. Instead of one long request, the page starts a
job, asks for its latest progress message every second, and loads the
result when the job is done. Jobs live in memory: this is a small app run on
one laptop, and nothing about a plan is saved.

Every job ends (issue #31): if the work crashes, even with something like
KeyboardInterrupt, it ends with a friendly error, and a job still running
after PLAN_TIME_LIMIT_SECONDS is ended with "This is taking too long". The
work itself can't be stopped from outside its thread, so it may carry on in
the background; whatever it returns after that is ignored.
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
PLAN_TIME_LIMIT_SECONDS = 4 * 60  # a plan normally takes about a minute

RUNNING, DONE = "running", "done"

FAILED = "Something went wrong making the plan. Please try again."
TOO_LONG = "This is taking too long. Please try again."


@dataclass
class Job:
    id: str
    state: str = RUNNING
    message: str = stages.STARTING
    page: dict = field(default_factory=dict)  # what the result page is rendered with
    status_code: int = 200
    started: float = field(default_factory=time.monotonic)
    fallback_page: dict = field(default_factory=dict)  # shown with an error if the job can't finish


# The work for one plan: given a way to report progress, returns (page context, HTTP status).
Work = Callable[[Callable[[str], None]], tuple[dict, int]]


class Jobs:
    def __init__(self, keep_seconds: float = KEEP_SECONDS, run_in_background: bool = True,
                 time_limit: float = PLAN_TIME_LIMIT_SECONDS):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self.keep_seconds = keep_seconds
        self.run_in_background = run_in_background  # tests run jobs straight away
        self.time_limit = time_limit

    def start(self, work: Work, fallback_page: dict) -> Job:
        """Start `work` and return its job. If it crashes, the job ends with `fallback_page` and a 500."""
        self._forget_old()
        job = Job(id=secrets.token_urlsafe(16), fallback_page=fallback_page)
        with self._lock:
            self._jobs[job.id] = job

        def report(message: str) -> None:
            if job.state == RUNNING:
                job.message = message

        def run() -> None:
            # Assume the worst, so the job is marked done (in `finally`) however the work ends.
            page, code = {**fallback_page, "error": FAILED}, 500
            try:
                page, code = work(report)
            except Exception:
                logger.exception("Plan job %s failed", job.id)
            except BaseException:
                # KeyboardInterrupt or SystemExit: end the job with the friendly error,
                # then let it carry on so the app can still be stopped.
                logger.warning("Plan job %s was interrupted", job.id)
                raise
            finally:
                self._finish(job, page, code)

        if self.run_in_background:
            threading.Thread(target=run, name=f"plan-{job.id[:6]}", daemon=True).start()
        else:
            run()
        return job

    def get(self, job_id: str) -> Job | None:
        """The job, ended with "This is taking too long" if it has run past the time limit."""
        with self._lock:
            job = self._jobs.get(job_id)
        if job is not None and job.state == RUNNING and time.monotonic() - job.started > self.time_limit:
            if self._finish(job, {**job.fallback_page, "error": TOO_LONG}, 504, message=TOO_LONG):
                logger.warning("Plan job %s ran past %s s and was given up on", job.id, self.time_limit)
        return job

    def _finish(self, job: Job, page: dict, code: int, message: str = stages.DONE) -> bool:
        """Mark `job` done with this result. Returns False (and changes nothing) if it was already done."""
        with self._lock:
            if job.state == DONE:
                return False
            job.page, job.status_code = page, code
            job.message, job.state = message, DONE
            return True

    def _forget_old(self) -> None:
        cutoff = time.monotonic() - self.keep_seconds
        with self._lock:
            for job_id in [j.id for j in self._jobs.values() if j.started < cutoff]:
                del self._jobs[job_id]
