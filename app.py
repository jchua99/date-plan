"""The page the pair look at together."""

import logging
from datetime import date

import pydantic
from dotenv import load_dotenv
from flask import Flask, render_template, request

from itinerary import CUISINES, NO_PREFERENCE, PlanRequest
from planner import PlanError, suggest_plan
from travel import plan_travel


def create_app(planner=suggest_plan, travel=plan_travel) -> Flask:
    app = Flask(__name__)

    @app.context_processor
    def cuisines():
        return {"cuisines": CUISINES}

    @app.get("/")
    def home():
        form = {"day": date.today().isoformat(), "start_time": "14:00", "area": "", "cuisine": NO_PREFERENCE, "start_from": ""}
        return render_template("index.html", form=form)

    @app.post("/plan")
    def plan():
        form = request.form.to_dict()
        try:
            plan_request = PlanRequest(**form)
            if not plan_request.area.strip():
                raise ValueError
        except (pydantic.ValidationError, ValueError, TypeError):
            error = "Please fill in the day, a start time and an area."
            return render_template("index.html", form=form, error=error), 400

        try:
            itinerary = planner(plan_request)
        except PlanError as problem:
            return render_template("index.html", form=form, error=str(problem)), 502

        try:
            trip = travel(itinerary, plan_request)
        except Exception:
            app.logger.exception("Travel lookup failed")
            trip = None  # travel problems never block the plan

        return render_template("index.html", form=form, itinerary=itinerary, plan_request=plan_request, travel=trip)

    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    load_dotenv()
    # Port 5000 is taken by AirPlay on macOS, so use 5001.
    create_app().run(host="127.0.0.1", port=5001, debug=True)
