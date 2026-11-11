from datetime import date, time

import pytest

from itinerary import Itinerary, ItineraryError, MissingCuisineError, PlanRequest, Stop, check_itinerary

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


def test_first_stop_may_start_a_little_late():
    plan = make("14:15", "15:30", "17:00")
    assert check_itinerary(plan, REQUEST) is plan


def test_first_stop_must_start_at_the_start_time():
    with pytest.raises(ItineraryError, match="Stop 1 starts at 18:00, not at 14:00"):
        check_itinerary(make("18:00", "19:00", "20:00"), REQUEST)


def test_plan_that_is_far_too_short_is_rejected():
    with pytest.raises(ItineraryError, match="only lasts 2 minutes"):
        check_itinerary(make("14:00", "14:01", "14:02"), REQUEST)


def test_plan_that_is_far_too_long_is_rejected():
    with pytest.raises(ItineraryError, match="far more than 3-4 hours"):
        check_itinerary(make("14:00", "17:00", "19:30"), REQUEST)


@pytest.mark.parametrize("last", ["15:30", "19:00"])
def test_plan_length_has_some_tolerance(last):
    plan = make("14:00", "15:00", last)
    assert check_itinerary(plan, REQUEST) is plan


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
