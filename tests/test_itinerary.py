import re
from datetime import date, time

import pydantic
import pytest
from openai.lib._pydantic import to_strict_json_schema

from itinerary import (
    MAX_STOPS,
    MIN_STOPS,
    Itinerary,
    ItineraryError,
    MissingCuisineError,
    PlannedItinerary,
    PlannedStop,
    PlanRequest,
    Stop,
    check_itinerary,
)

REQUEST = PlanRequest(day=date(2026, 11, 14), start_time=time(14, 0), area="Tiong Bahru")


def make(*times, activity="Coffee", place="Tiong Bahru Bakery"):
    stops = [Stop(time=t, activity=activity, place=place, note="") for t in times]
    return Itinerary(title="Saturday", stops=stops)


def test_good_itinerary_passes():
    plan = make("14:00", "15:30", "17:00")
    assert check_itinerary(plan, REQUEST) is plan


@pytest.mark.parametrize("times", [("14:00", "15:00"), ("14:00", "15", "16", "17", "18", "19")])
def test_too_few_or_too_many_stops(times):
    times = tuple(t if ":" in t else f"{t}:00" for t in times)
    with pytest.raises(ItineraryError, match="3-5 stops"):
        check_itinerary(make(*times), REQUEST)


def test_times_must_go_forward():
    with pytest.raises(ItineraryError, match="Stop 3"):
        check_itinerary(make("14:00", "16:00", "15:00"), REQUEST)


def test_same_time_twice_is_rejected():
    with pytest.raises(ItineraryError, match="Stop 2"):
        check_itinerary(make("14:00", "14:00", "15:00"), REQUEST)


def test_nothing_before_start_time():
    with pytest.raises(ItineraryError, match="before 14:00"):
        check_itinerary(make("13:30", "15:00", "16:00"), REQUEST)


def test_bad_time_text():
    with pytest.raises(ItineraryError, match="not a time"):
        check_itinerary(make("2pm", "15:00", "16:00"), REQUEST)


def test_blank_place_is_rejected():
    with pytest.raises(ItineraryError, match="missing"):
        check_itinerary(make("14:00", "15:00", "16:00", place="  "), REQUEST)


def test_cuisine_defaults_to_no_preference():
    assert REQUEST.cuisine == "No preference"


def test_unknown_cuisine_is_rejected():
    with pytest.raises(ValueError, match="not one of the cuisines"):
        PlanRequest(day=date(2026, 11, 14), start_time=time(14, 0), area="Tiong Bahru", cuisine="Martian")


def test_picked_cuisine_must_appear_in_a_stop():
    malay = REQUEST.model_copy(update={"cuisine": "Malay"})
    with pytest.raises(MissingCuisineError, match="Malay"):
        check_itinerary(make("14:00", "15:30", "17:00"), malay)


def test_picked_cuisine_found_in_any_stop_ignoring_case():
    malay = REQUEST.model_copy(update={"cuisine": "Malay"})
    plan = make("14:00", "15:30", "17:00")
    plan.stops[2].note = "Share nasi lemak at a MALAY stall"
    assert check_itinerary(plan, malay) is plan


# --- What the model writes (structured output) ---

PLANNED_STOP = dict(time="14:00", activity="Coffee", place="Tiong Bahru Bakery", note="Easy start",
                    reservable=True, booking_url="https://example.com/book")


def planned(times=("14:00", "15:00", "16:00")):
    return PlannedItinerary(title="T", stops=[PlannedStop(**{**PLANNED_STOP, "time": t}) for t in times])


def test_planned_schema_is_strict_and_only_has_model_fields():
    schema = to_strict_json_schema(PlannedItinerary)
    stop = schema["$defs"]["PlannedStop"]
    assert schema["additionalProperties"] is False and stop["additionalProperties"] is False
    assert set(stop["properties"]) == {"time", "activity", "place", "note", "reservable", "booking_url"}
    assert set(stop["required"]) == set(stop["properties"])


def test_planned_schema_limits_stops_and_time_shape():
    schema = to_strict_json_schema(PlannedItinerary)
    stops = schema["properties"]["stops"]
    assert (stops["minItems"], stops["maxItems"]) == (MIN_STOPS, MAX_STOPS)
    time_pattern = schema["$defs"]["PlannedStop"]["properties"]["time"]["pattern"]
    assert re.fullmatch(time_pattern, "09:30") and not re.fullmatch(time_pattern, "9.30pm")


@pytest.mark.parametrize("bad", ["2pm", "24:00", "14:60", "14:0"])
def test_planned_stop_rejects_odd_times(bad):
    with pytest.raises(pydantic.ValidationError):
        PlannedStop(**{**PLANNED_STOP, "time": bad})


def test_planned_itinerary_rejects_extra_keys():
    with pytest.raises(pydantic.ValidationError):
        PlannedItinerary.model_validate({"title": "T", "stops": [PLANNED_STOP] * 3, "maps_url": "x"})


def test_planned_itinerary_becomes_an_unchecked_itinerary():
    plan = planned().to_itinerary()
    assert isinstance(plan, Itinerary)
    assert [s.time for s in plan.stops] == ["14:00", "15:00", "16:00"]
    assert plan.stops[0].booking_url == "https://example.com/book" and plan.stops[0].reservable
    assert all(s.maps_url == "" and not s.checked for s in plan.stops)
