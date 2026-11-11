import pytest

from app import create_app
from planner import PlanError
from tests.test_planner import GOOD

FORM = {"day": "2026-11-14", "start_time": "14:00", "area": "Tiong Bahru"}


def client_with(planner):
    return create_app(planner=planner).test_client()


def test_home_shows_the_form():
    page = client_with(lambda r: GOOD).get("/").get_data(as_text=True)
    assert "Suggest a plan" in page
    assert 'name="area"' in page
    assert '<select name="cuisine">' in page
    for cuisine in ("Chinese", "Indian", "Malay", "Mexican"):
        assert f'<option value="{cuisine}">' in page
    assert '<option value="No preference" selected>' in page


def test_picked_cuisine_reaches_planner_and_stays_selected():
    asked = []
    response = client_with(lambda r: asked.append(r) or GOOD).post("/plan", data={**FORM, "cuisine": "Indian"})
    assert response.status_code == 200
    assert asked[0].cuisine == "Indian"
    assert '<option value="Indian" selected>' in response.get_data(as_text=True)


def test_unknown_cuisine_asks_again_without_calling_the_model():
    calls = []
    response = client_with(lambda r: calls.append(r)).post("/plan", data={**FORM, "cuisine": "Martian"})
    assert response.status_code == 400
    assert "Please fill in" in response.get_data(as_text=True)
    assert calls == []


def test_plan_shows_itinerary_with_times_and_places():
    asked = []
    response = client_with(lambda r: asked.append(r) or GOOD).post("/plan", data=FORM)
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "A slow Saturday in Tiong Bahru" in page
    for stop in GOOD.stops:
        assert stop.time in page and stop.activity in page and stop.place in page
    assert asked[0].area == "Tiong Bahru"


@pytest.mark.parametrize("missing", ["day", "start_time", "area"])
def test_missing_field_asks_again_without_calling_the_model(missing):
    calls = []
    form = {**FORM, missing: " " if missing == "area" else ""}
    response = client_with(lambda r: calls.append(r)).post("/plan", data=form)
    assert response.status_code == 400
    assert "Please fill in" in response.get_data(as_text=True)
    assert calls == []


def test_planner_problem_is_shown_and_form_kept():
    def failing(_):
        raise PlanError("Couldn't make a plan this time. Please try again.")

    response = client_with(failing).post("/plan", data=FORM)
    page = response.get_data(as_text=True)
    assert response.status_code == 502
    assert "Couldn&#39;t make a plan" in page
    assert 'value="Tiong Bahru"' in page


def test_cuisine_kept_after_planner_problem():
    def failing(_):
        raise PlanError("Couldn't make a plan this time. Please try again.")

    response = client_with(failing).post("/plan", data={**FORM, "cuisine": "Thai"})
    assert '<option value="Thai" selected>' in response.get_data(as_text=True)
