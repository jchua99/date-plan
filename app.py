"""The page the pair look at together."""

import inspect
import logging
from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pydantic
from dotenv import load_dotenv
from flask import Flask, abort, jsonify, render_template, request, url_for

import progress as stages
from itinerary import CUISINES, NO_PREFERENCE, PlanRequest
from jobs import DONE, Jobs
from planner import PlanError, suggest_plan
from travel import plan_travel

EXPIRED = "That plan is no longer available. Please make a new one."
DAY_PASSED = "That day has already passed. Please pick today or a later day."
TIME_PASSED = "That start time has already passed. Please pick a later time."


def singapore_time() -> tzinfo:
    """Singapore's time zone, wherever the server runs."""
    try:
        return ZoneInfo("Asia/Singapore")
    except ZoneInfoNotFoundError:
        # Windows has no time zone list unless the tzdata package is installed.
        # Singapore has been UTC+8 all year round since 1982, so this is the same.
        return timezone(timedelta(hours=8), "SGT")


SINGAPORE = singapore_time()


def singapore_now() -> datetime:
    """The date and time in Singapore right now."""
    return datetime.now(SINGAPORE)


def _takes_progress(fn) -> bool:
    """True if `fn` accepts a progress callback (test stand-ins often don't)."""
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.name == "progress" or p.kind is p.VAR_KEYWORD for p in params)


def create_app(planner=suggest_plan, travel=plan_travel, jobs: Jobs | None = None, now=singapore_now) -> Flask:
    """`now()` gives the current time in Singapore. Tests pass a fixed one."""
    app = Flask(__name__)
    jobs = jobs or Jobs()

    def blank_form() -> dict:
        """The form as first shown: today in Singapore, from 14:00."""
        return {"day": now().date().isoformat(), "start_time": "14:00", "area": "", "cuisine": NO_PREFERENCE, "start_from": ""}

    def make_plan(form: dict, progress=stages.ignore) -> tuple[dict, int]:
        """Everything a plan needs, as (page context, HTTP status). Used by both ways of asking."""
        try:
            plan_request = PlanRequest(**form)
            if not plan_request.area.strip():
                raise ValueError
        except (pydantic.ValidationError, ValueError, TypeError):
            return {"form": form, "error": "Please fill in the day, a start time and an area."}, 400

        # Only plan from now on. Times are Singapore's, wherever the server is.
        right_now = now()
        if plan_request.day < right_now.date():
            return {"form": form, "error": DAY_PASSED}, 400
        if plan_request.day == right_now.date() and plan_request.start_time < right_now.time().replace(second=0, microsecond=0):
            return {"form": form, "error": TIME_PASSED}, 400

        try:
            if _takes_progress(planner):
                itinerary = planner(plan_request, progress=progress)
            else:
                itinerary = planner(plan_request)
        except PlanError as problem:
            return {"form": form, "error": str(problem)}, 502

        progress(stages.TRAVEL)
        try:
            trip = travel(itinerary, plan_request)
        except Exception:
            app.logger.exception("Travel lookup failed")
            trip = None  # travel problems never block the plan

        return {"form": form, "itinerary": itinerary, "plan_request": plan_request, "travel": trip}, 200

    @app.context_processor
    def cuisines():
        return {"cuisines": CUISINES}

    @app.get("/")
    def home():
        return render_template("index.html", form=blank_form())

    @app.post("/plan")
    def plan():
        """The plain form post: waits for the whole plan. Used when the page's script can't run."""
        page, code = make_plan(request.form.to_dict())
        return render_template("index.html", **page), code

    @app.post("/plan/start")
    def start_plan():
        """Start making a plan in the background; the page then follows its progress."""
        form = request.form.to_dict()
        job = jobs.start(lambda report: make_plan(form, report), fallback_page={"form": form})
        return jsonify(
            status_url=url_for("plan_status", job_id=job.id),
            result_url=url_for("plan_result", job_id=job.id),
        ), 202

    @app.get("/plan/status/<job_id>")
    def plan_status(job_id):
        job = jobs.get(job_id)
        if job is None:
            return jsonify(done=True, message=EXPIRED), 404
        return jsonify(done=job.state == DONE, message=job.message)

    @app.get("/plan/result/<job_id>")
    def plan_result(job_id):
        job = jobs.get(job_id)
        if job is None or job.state != DONE:
            return render_template("index.html", form=blank_form(), error=EXPIRED), 404
        return render_template("index.html", **job.page), job.status_code

    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    load_dotenv()
    # Port 5000 is taken by AirPlay on macOS, so use 5001.
    create_app().run(host="127.0.0.1", port=5001, debug=True)
