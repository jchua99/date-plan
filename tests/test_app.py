from datetime import datetime, timedelta, timezone

import pytest

import app as app_module
from app import SINGAPORE, create_app, singapore_now, singapore_time
from planner import PlanError
from tests.test_planner import GOOD
from tests.test_travel import HAVELOCK_TRIP, FakeOneMap
from travel import Travel, TravelError, plan_travel

FORM = {"day": "2026-11-14", "start_time": "14:00", "area": "Tiong Bahru"}
# A fixed "now" in Singapore a few days before FORM's day, so tests don't depend on today's date.
BEFORE_THE_PLAN = datetime(2026, 11, 11, 9, 30, tzinfo=SINGAPORE)


def at(when):
    return lambda: when


def no_travel(itinerary, request):
    return Travel(places=[None] * len(itinerary.stops), legs=[])


def client_with(planner, travel=no_travel, now=BEFORE_THE_PLAN):
    return create_app(planner=planner, travel=travel, now=at(now)).test_client()


def fake_travel(itinerary, request):
    return plan_travel(itinerary, request, client=FakeOneMap())


# --- Dates are Singapore's, and plans can't be in the past ---


def test_default_day_is_today_in_singapore():
    # 07:00 in Singapore is still the day before on a UTC server.
    early = datetime(2026, 11, 12, 7, 0, tzinfo=SINGAPORE)
    page = client_with(lambda r: GOOD, now=early).get("/").get_data(as_text=True)
    assert 'value="2026-11-12"' in page


def test_singapore_now_is_utc_plus_8():
    assert singapore_now().utcoffset() == timedelta(hours=8)


def test_falls_back_to_utc_plus_8_without_a_time_zone_list(monkeypatch):
    def missing(key):
        raise app_module.ZoneInfoNotFoundError(key)

    monkeypatch.setattr(app_module, "ZoneInfo", missing)
    assert singapore_time() == timezone(timedelta(hours=8))


def test_day_in_the_past_is_refused():
    calls = []
    response = client_with(lambda r: calls.append(r), now=datetime(2026, 11, 15, 9, 0, tzinfo=SINGAPORE)).post("/plan", data=FORM)
    assert response.status_code == 400
    assert "That day has already passed" in response.get_data(as_text=True)
    assert calls == []


def test_start_time_already_passed_today_is_refused():
    calls = []
    response = client_with(lambda r: calls.append(r), now=datetime(2026, 11, 14, 14, 5, tzinfo=SINGAPORE)).post("/plan", data=FORM)
    assert response.status_code == 400
    assert "That start time has already passed" in response.get_data(as_text=True)
    assert calls == []


@pytest.mark.parametrize("now", [datetime(2026, 11, 14, 13, 0), datetime(2026, 11, 14, 14, 0, 45)])
def test_later_today_is_planned(now):
    response = client_with(lambda r: GOOD, now=now.replace(tzinfo=SINGAPORE)).post("/plan", data=FORM)
    assert response.status_code == 200


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


def test_reservable_stops_get_a_reserve_link_that_opens_in_a_new_tab():
    stops = [
        GOOD.stops[0].model_copy(update={"reservable": True, "booking_url": "https://good.example/book"}),
        GOOD.stops[1],
        GOOD.stops[2].model_copy(update={"reservable": True}),
    ]
    itinerary = GOOD.model_copy(update={"stops": stops})
    page = client_with(lambda r: itinerary).post("/plan", data=FORM).get_data(as_text=True)
    assert page.count("Reserve ↗") == 2
    assert 'href="https://good.example/book" target="_blank" rel="noopener noreferrer"' in page
    assert "https://www.google.com/search?q=BooksActually+reservation" in page
    assert page.count("Opens a search for its booking page") == 1


def test_form_has_optional_starting_point():
    page = client_with(lambda r: GOOD).get("/").get_data(as_text=True)
    assert 'name="start_from"' in page


def test_plan_shows_travel_between_stops_and_the_map():
    page = client_with(lambda r: GOOD, fake_travel).post("/plan", data=FORM).get_data(as_text=True)
    assert "Leave by 14:23" in page and "37 min" in page
    assert "Walk 9 min → MRT TE line" in page
    assert "https://www.google.com/maps/dir/?api=1&amp;origin=Tiong+Bahru+Bakery" in page
    assert 'id="map"' in page
    assert "onemap.gov.sg/maps/tiles" in page


def test_starting_point_is_passed_on_and_shown_before_stop_one():
    asked = []

    def travel(itinerary, request):
        asked.append(request)
        return fake_travel(itinerary, request)

    form = {**FORM, "start_from": "Outram Park MRT"}
    page = client_with(lambda r: GOOD, travel).post("/plan", data=form).get_data(as_text=True)
    assert asked[0].start_from == "Outram Park MRT"
    assert "From Outram Park MRT" in page and "Leave by 13:23" in page
    assert 'value="Outram Park MRT"' in page


def test_tight_connection_is_flagged():
    def slow(itinerary, request):
        return plan_travel(itinerary, request, client=FakeOneMap(trip={**HAVELOCK_TRIP, "duration": 70 * 60}))

    page = client_with(lambda r: GOOD, slow).post("/plan", data=FORM).get_data(as_text=True)
    assert "Tight connection" in page


def test_travel_problem_shows_on_leg_with_google_link_and_no_map():
    def broken(itinerary, request):
        return plan_travel(itinerary, request, client=FakeOneMap(error=TravelError("Couldn't reach OneMap.")))

    page = client_with(lambda r: GOOD, broken).post("/plan", data=FORM).get_data(as_text=True)
    assert "A slow Saturday in Tiong Bahru" in page
    assert "Couldn&#39;t reach OneMap." in page
    assert "google.com/maps/dir" in page
    assert 'id="map"' not in page


def test_plan_still_shows_if_travel_crashes():
    def crashing(itinerary, request):
        raise RuntimeError("bug")

    response = client_with(lambda r: GOOD, crashing).post("/plan", data=FORM)
    assert response.status_code == 200
    assert "A slow Saturday in Tiong Bahru" in response.get_data(as_text=True)
