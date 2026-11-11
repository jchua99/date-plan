import json
from datetime import date, time
from types import SimpleNamespace

import httpx2
import openai
import pytest

from itinerary import Itinerary, PlanRequest, Stop
from reservations import PlaceCheck, place_name
from planner import SYSTEM_PROMPT, PlanError, build_prompt, read_itinerary, suggest_plan

REQUEST = PlanRequest(day=date(2026, 11, 14), start_time=time(14, 0), area="Tiong Bahru")

GOOD = Itinerary(
    title="A slow Saturday in Tiong Bahru",
    stops=[
        Stop(time="14:00", activity="Coffee", place="Tiong Bahru Bakery", note="Easy start"),
        Stop(time="15:00", activity="Mural walk", place="Tiong Bahru estate", note="Side by side"),
        Stop(time="16:30", activity="Bookshop browse", place="BooksActually", note="Swap picks"),
    ],
)


class FakeClient:
    """Stands in for openai.OpenAI so tests run offline and free."""

    def __init__(self, reply=GOOD.model_dump_json(), model="glm-5.3-flash", error=None):
        self.calls = []
        self._reply, self._model, self._error = reply, model, error
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        message = SimpleNamespace(content=self._reply)
        return SimpleNamespace(model=self._model, choices=[SimpleNamespace(message=message)])


def api_error(cls, status):
    request = httpx2.Request("POST", "https://opencode.ai/inference/openai/v1/chat/completions")
    return cls("boom", response=httpx2.Response(status, request=request), body=None)


def test_returns_checked_plan():
    assert suggest_plan(REQUEST, client=FakeClient()) == GOOD


def test_asks_glm_flash_with_pair_details():
    client = FakeClient()
    suggest_plan(REQUEST, client=client)
    call = client.calls[0]
    assert call["model"] == "glm-5.3-flash"
    system, user = call["messages"]
    assert system["role"] == "system" and "JSON" in system["content"]
    assert user["content"] == build_prompt(REQUEST)


def test_prompt_text():
    assert build_prompt(REQUEST) == "Day: Saturday 14 November 2026\nFree from: 14:00\nArea: Tiong Bahru"


def test_prompt_includes_picked_cuisine():
    malay = REQUEST.model_copy(update={"cuisine": "Malay"})
    assert build_prompt(malay).endswith("\nCuisine: Malay")


def test_system_prompt_asks_for_a_meal_of_the_cuisine():
    assert "meal" in SYSTEM_PROMPT and "cuisine" in SYSTEM_PROMPT


def test_plan_with_picked_cuisine_is_accepted():
    plan = GOOD.model_copy(deep=True)
    plan.stops[2].activity = "Malay dinner"
    malay = REQUEST.model_copy(update={"cuisine": "Malay"})
    assert suggest_plan(malay, client=FakeClient(reply=plan.model_dump_json())) == plan


def test_plan_missing_picked_cuisine_becomes_friendly_error():
    malay = REQUEST.model_copy(update={"cuisine": "Malay"})
    with pytest.raises(PlanError, match="didn't include a Malay meal"):
        suggest_plan(malay, client=FakeClient())


def test_reply_from_another_model_is_rejected():
    with pytest.raises(PlanError, match="not GLM-5.3 Flash"):
        suggest_plan(REQUEST, client=FakeClient(model="kimi-k3"))


def test_reads_json_wrapped_in_code_fences():
    assert read_itinerary(f"Here you go:\n```json\n{GOOD.model_dump_json()}\n```") == GOOD


@pytest.mark.parametrize("reply", [None, "", "Sorry, I can't help", '{"title": "No stops"}'])
def test_unreadable_reply_becomes_friendly_error(reply):
    with pytest.raises(PlanError, match="try again"):
        suggest_plan(REQUEST, client=FakeClient(reply=reply))


def test_plan_breaking_rules_becomes_friendly_error():
    bad = Itinerary(title="Too short", stops=GOOD.stops[:1])
    with pytest.raises(PlanError, match="didn't quite add up"):
        suggest_plan(REQUEST, client=FakeClient(reply=bad.model_dump_json()))


def test_missing_key_becomes_friendly_error(monkeypatch):
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    with pytest.raises(PlanError, match="No OpenCode API key"):
        suggest_plan(REQUEST)


@pytest.mark.parametrize(
    "error, message",
    [
        (api_error(openai.AuthenticationError, 401), "API key"),
        (api_error(openai.PermissionDeniedError, 403), "Turn on GLM-5.3 Flash"),
        (api_error(openai.RateLimitError, 429), "Too many requests"),
        (openai.APIConnectionError(request=httpx2.Request("POST", "https://x")), "internet"),
        (api_error(openai.InternalServerError, 500), "try again"),
    ],
)
def test_api_errors_become_friendly_errors(error, message):
    with pytest.raises(PlanError, match=message):
        suggest_plan(REQUEST, client=FakeClient(error=error))


def test_prompt_asks_which_stops_take_bookings():
    client = FakeClient()
    suggest_plan(REQUEST, client=client)
    assert '"reservable"' in client.calls[0]["messages"][0]["content"]


def test_reservable_defaults_to_false_when_model_leaves_it_out():
    reply = '{"title": "T", "stops": [{"time": "14:00", "activity": "a", "place": "p", "note": "n"}]}'
    assert read_itinerary(reply).stops[0].reservable is False


def test_prompt_asks_for_booking_url():
    client = FakeClient()
    suggest_plan(REQUEST, client=client)
    assert '"booking_url"' in client.calls[0]["messages"][0]["content"]


def test_booking_links_are_checked_before_plan_is_returned():
    checked = []

    def checker(itinerary):
        checked.append(itinerary)
        return itinerary

    assert suggest_plan(REQUEST, client=FakeClient(), link_checker=checker) == GOOD
    assert checked == [GOOD]


def test_planning_model_cannot_fill_in_checked_fields():
    stop = {"time": "14:00", "activity": "a", "place": "p", "note": "n", "reservable": True,
            "maps_url": "https://evil.example", "checked": True}
    reply = json.dumps({"title": "T", "stops": [stop, {**stop, "time": "15:00"}, {**stop, "time": "16:00"}]})
    plan = suggest_plan(REQUEST, client=FakeClient(reply=reply), link_checker=lambda it: it)
    assert all(s.maps_url == "" and not s.checked for s in plan.stops)


def test_harness_is_used_when_places_key_is_set(monkeypatch):
    import planner

    used = []

    class FakeHarness:
        def __init__(self, client, model, key):
            used.append((model, key))

        def check_itinerary(self, itinerary, area):
            used.append(area)
            return itinerary

    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "k")
    monkeypatch.setattr(planner, "ReservationHarness", FakeHarness)
    suggest_plan(REQUEST, client=FakeClient())
    assert used == [("glm-5.3-flash", "k"), "Tiong Bahru"]


def test_empty_reply_is_retried_once():
    replies = ["", GOOD.model_dump_json()]
    client = FakeClient()
    original = client._create

    def create(**kwargs):
        client._reply = replies.pop(0)
        return original(**kwargs)

    client.chat.completions.create = create
    assert suggest_plan(REQUEST, client=client, link_checker=lambda it: it) == GOOD
    assert len(client.calls) == 2


def test_gives_up_after_two_unreadable_replies():
    client = FakeClient(reply="")
    with pytest.raises(PlanError, match="try again"):
        suggest_plan(REQUEST, client=client, link_checker=lambda it: it)
    assert len(client.calls) == 2


def test_leaves_room_for_reasoning():
    client = FakeClient()
    suggest_plan(REQUEST, client=client, link_checker=lambda it: it)
    assert client.calls[0]["max_tokens"] >= 16000


# --- Checking places on Google Maps ---

CLOSED_PLAN = Itinerary(
    title="T",
    stops=[*GOOD.stops[:2], Stop(time="16:30", activity="Dinner", place="Muchacho, 60 Tras Street", note="n"), GOOD.stops[2].model_copy(update={"time": "18:00"})],
)


class ReplyQueue(FakeClient):
    """Gives each reply in turn."""

    def __init__(self, *replies):
        super().__init__()
        self.replies = [r.model_dump_json() for r in replies]

    def _create(self, **kwargs):
        self._reply = self.replies.pop(0)
        return super()._create(**kwargs)


def google_says(closed=(), listings=None):
    """Fake place checker: names in `closed` have closed; `listings` maps names to Google listings."""
    listings = listings or {}

    def check(itinerary, area):
        return [
            PlaceCheck(i, s.place, "has closed for good" if place_name(s.place) in closed else "", listings.get(place_name(s.place)))
            for i, s in enumerate(itinerary.stops)
        ]

    return check


def plan_with(place_checker, client=None):
    return suggest_plan(REQUEST, client=client or FakeClient(), link_checker=lambda it: it, place_checker=place_checker)


def test_plan_with_a_closed_place_is_asked_for_again_naming_it():
    client = ReplyQueue(CLOSED_PLAN, GOOD)
    plan = plan_with(google_says(closed={"Muchacho"}), client)
    assert [s.place for s in plan.stops] == [s.place for s in GOOD.stops]
    assert "have closed, so don't suggest them: Muchacho, 60 Tras Street" in client.calls[1]["messages"][1]["content"]
    assert "closed" not in client.calls[0]["messages"][1]["content"]


def test_closed_place_is_removed_if_it_keeps_coming_back():
    client = ReplyQueue(CLOSED_PLAN, CLOSED_PLAN, CLOSED_PLAN)
    plan = plan_with(google_says(closed={"Muchacho"}), client)
    assert len(client.calls) == 3
    assert [s.place for s in plan.stops] == [s.place for s in GOOD.stops]


class ThenUnreadable(ReplyQueue):
    """Gives each reply in turn, then replies that can't be read as a plan."""

    def _create(self, **kwargs):
        if not self.replies:
            self._reply = "Sorry, I ran out of room to answer."
            return FakeClient._create(self, **kwargs)
        return super()._create(**kwargs)


def test_unreadable_replan_falls_back_to_the_last_plan_without_its_closed_place():
    """Issue #32: a failed replan used to throw away a usable plan."""
    client = ThenUnreadable(CLOSED_PLAN)
    plan = plan_with(google_says(closed={"Muchacho"}), client)
    assert [s.place for s in plan.stops] == [s.place for s in GOOD.stops]
    assert len(client.calls) == 3  # the first plan, then both tries at a new one


def test_rule_breaking_replan_falls_back_to_the_last_plan_without_its_closed_place():
    too_short = GOOD.model_copy(update={"stops": GOOD.stops[:1]})
    client = ReplyQueue(CLOSED_PLAN, too_short)
    plan = plan_with(google_says(closed={"Muchacho"}), client)
    assert [s.place for s in plan.stops] == [s.place for s in GOOD.stops]


def test_failed_replan_still_gives_up_if_the_fallback_has_too_few_stops():
    client = ThenUnreadable(GOOD)
    with pytest.raises(PlanError, match="closed"):
        plan_with(google_says(closed={GOOD.stops[0].place}), client)


def test_unreadable_first_plan_is_still_an_error():
    client = ThenUnreadable()
    with pytest.raises(PlanError, match="Couldn't make a plan"):
        plan_with(google_says(), client)


def test_gives_up_if_removing_closed_places_leaves_too_few_stops():
    client = ReplyQueue(GOOD, GOOD, GOOD)
    with pytest.raises(PlanError, match="closed"):
        plan_with(google_says(closed={s.place for s in GOOD.stops}), client)


def test_open_plan_is_not_asked_again():
    client = FakeClient()
    plan_with(google_says(), client)
    assert len(client.calls) == 1


def test_plan_shows_google_address_not_the_planners():
    bakery = {
        "id": "tbb-1",
        "displayName": {"text": "Tiong Bahru Bakery"},
        "formattedAddress": "56 Eng Hoon St, #01-70, Singapore 160056",
        "googleMapsUri": "https://maps.google.com/?cid=7",
        "businessStatus": "OPERATIONAL",
    }
    first = GOOD.stops[0].model_copy(update={"place": "Tiong Bahru Bakery, 1 Made Up Road"})
    planned = GOOD.model_copy(update={"stops": [first, *GOOD.stops[1:]]})
    stops = plan_with(google_says(listings={"Tiong Bahru Bakery": bakery}), FakeClient(reply=planned.model_dump_json())).stops
    assert (stops[0].place, stops[0].address, stops[0].place_id) == (
        "Tiong Bahru Bakery",
        "56 Eng Hoon St, #01-70, Singapore 160056",
        "tbb-1",
    )
    assert all(s.address == "" for s in stops[1:])  # not on Google: no address shown


def test_planning_model_cannot_fill_in_google_fields():
    stop = {"time": "14:00", "activity": "a", "place": "p", "note": "n",
            "address": "1 Fake St", "place_id": "fake", "maps_url": "https://evil.example", "checked": True}
    reply = json.dumps({"title": "T", "stops": [stop, {**stop, "time": "15:00"}, {**stop, "time": "16:00"}]})
    plan = suggest_plan(REQUEST, client=FakeClient(reply=reply), link_checker=lambda it: it)
    assert all(s.address == "" and s.place_id == "" and s.maps_url == "" and not s.checked for s in plan.stops)


def test_places_are_checked_on_google_when_key_is_set(monkeypatch):
    import planner

    seen = []

    def fake_check(itinerary, area, key):
        seen.append((area, key))
        return [PlaceCheck(i, s.place) for i, s in enumerate(itinerary.stops)]

    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "k")
    monkeypatch.setattr(planner, "check_places", fake_check)
    suggest_plan(REQUEST, client=FakeClient(), link_checker=lambda it: it)
    assert seen == [("Tiong Bahru", "k")]


def test_without_google_key_places_are_left_as_planned():
    assert suggest_plan(REQUEST, client=FakeClient(), link_checker=lambda it: it) == GOOD
