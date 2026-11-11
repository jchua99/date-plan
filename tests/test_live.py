"""Calls GLM-5.3 Flash through OpenCode for real. Skipped unless you run: pytest --live"""

import logging
from datetime import date, time

import pytest
from dotenv import load_dotenv

from itinerary import MAX_STOPS, MIN_STOPS, PlanRequest
from planner import suggest_plan


@pytest.mark.live
def test_real_plan_comes_back_as_structured_output(caplog):
    load_dotenv()
    request = PlanRequest(day=date.today(), start_time=time(14, 0), area="Tiong Bahru", cuisine="Malay")
    with caplog.at_level(logging.WARNING):
        plan = suggest_plan(request, link_checker=lambda it: it)  # skip the slow booking checks
    assert MIN_STOPS <= len(plan.stops) <= MAX_STOPS
    assert "falling back" not in caplog.text.lower(), "OpenCode didn't accept the strict schema"
