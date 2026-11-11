from datetime import date, time

import pytest

from itinerary import Itinerary, ItineraryError, MissingCuisineError, PlanRequest, Stop, check_itinerary, parse_time

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


@pytest.mark.parametrize("text, expected", [
    ("14:30", time(14, 30)),
    ("9:30", time(9, 30)),
    ("9:5", time(9, 5)),
    ("14:00:00", time(14, 0)),
    ("9.30", time(9, 30)),
    (" 7:30pm", time(19, 30)),
    ("7:30 PM", time(19, 30)),
    ("11:15 a.m.", time(11, 15)),
    ("12:00pm", time(12, 0)),
    ("12:30am", time(0, 30)),
])
def test_common_time_variants_are_read(text, expected):
    assert parse_time(text) == expected


@pytest.mark.parametrize("text", ["", "noon", "2pm", "25:00", "14:60", "13:00pm", "0:30am", "14:30:00:00", "14-30"])
def test_garbage_times_are_rejected(text):
    with pytest.raises(ItineraryError, match="not a time"):
        parse_time(text)


def test_times_are_stored_as_hh_mm():
    plan = check_itinerary(make("14:00:00", "3:30pm", "17.00"), REQUEST)
    assert [s.time for s in plan.stops] == ["14:00", "15:30", "17:00"]


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
