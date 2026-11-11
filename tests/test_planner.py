import json
from datetime import date, time
from types import SimpleNamespace

import httpx2
import openai
import pytest

from itinerary import Itinerary, PlannedItinerary, PlannedStop, PlanRequest, Stop
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


def as_reply(itinerary):
    """The plan as the model would write it: only the fields in its schema."""
    stops = [stop.model_dump(include=set(PlannedStop.model_fields)) for stop in itinerary.stops]
    return json.dumps({"title": itinerary.title, "stops": stops})


class FakeClient:
    """Stands in for openai.OpenAI so tests run offline and free.

    parse() acts like the SDK's structured-output call: it checks the reply
    against the schema it was given (raising like the SDK does) and returns it
    as message.parsed. create() is the old free-text call, used for the fallback.
    A reply can also be an exception, which that call raises.
    """

    def __init__(self, reply=as_reply(GOOD), model="glm-5.3-flash", error=None, replies=None, refusal=None):
        self.calls = []
        self._replies = list(replies) if replies is not None else None
        self._reply, self._model, self._error, self._refusal = reply, model, error, refusal
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create, parse=self._parse))

    def _next(self):
        reply = self._replies.pop(0) if self._replies is not None else self._reply
        if self._error:
            raise self._error
        if isinstance(reply, BaseException):
            raise reply
        return reply

    def _response(self, message):
        return SimpleNamespace(model=self._model, choices=[SimpleNamespace(message=message)])

    def _parse(self, **kwargs):
        self.calls.append({"via": "parse", **kwargs})
        reply = self._next()
        if self._refusal:
            return self._response(SimpleNamespace(content=None, refusal=self._refusal, parsed=None))
        parsed = kwargs["response_format"].model_validate_json(reply) if reply else None
        return self._response(SimpleNamespace(content=reply, refusal=None, parsed=parsed))

    def _create(self, **kwargs):
        self.calls.append({"via": "create", **kwargs})
        return self._response(SimpleNamespace(content=self._next()))


def truncated():
    return openai.LengthFinishReasonError(completion=SimpleNamespace(usage=None))


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
    assert suggest_plan(malay, client=FakeClient(reply=as_reply(plan))) == plan


def test_plan_missing_picked_cuisine_becomes_friendly_error():
    malay = REQUEST.model_copy(update={"cuisine": "Malay"})
    with pytest.raises(PlanError, match="didn't include a Malay meal"):
        suggest_plan(malay, client=FakeClient())


def test_reply_from_another_model_is_rejected():
    with pytest.raises(PlanError, match="not GLM-5.3 Flash"):
        suggest_plan(REQUEST, client=FakeClient(model="kimi-k3"))


def test_reads_json_wrapped_in_code_fences():
    assert read_itinerary(f"Here you go:\n```json\n{as_reply(GOOD)}\n```") == GOOD


@pytest.mark.parametrize("reply", [None, "", "Sorry, I can't help", '{"title": "No stops"}'])
def test_unreadable_reply_becomes_friendly_error(reply):
    with pytest.raises(PlanError, match="try again"):
        suggest_plan(REQUEST, client=FakeClient(reply=reply))


def test_plan_breaking_rules_becomes_friendly_error():
    bad = GOOD.model_copy(deep=True)
    bad.stops[2].time = "14:30"  # earlier than the stop before it
    with pytest.raises(PlanError, match="didn't quite add up"):
        suggest_plan(REQUEST, client=FakeClient(reply=as_reply(bad)))


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
            "address": "1 Fake St", "place_id": "fake", "maps_url": "https://evil.example", "checked": True}
    reply = json.dumps({"title": "T", "stops": [stop, {**stop, "time": "15:00"}, {**stop, "time": "16:00"}]})
    # Strict schema: extra keys aren't allowed, so this reply can't be read.
    with pytest.raises(PlanError, match="try again"):
        suggest_plan(REQUEST, client=FakeClient(reply=reply), link_checker=lambda it: it)
    # Old free-text fallback: the plan is read, but the checked fields are wiped.
    client = FakeClient(replies=[bad_request(), reply])
    plan = suggest_plan(REQUEST, client=client, link_checker=lambda it: it)
    assert all(s.address == "" and s.place_id == "" and s.maps_url == "" and not s.checked for s in plan.stops)


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
    client = FakeClient(replies=["", as_reply(GOOD)])
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


def ReplyQueue(*plans):
    """Gives each plan in turn."""
    return FakeClient(replies=[as_reply(p) for p in plans])


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
    stops = plan_with(google_says(listings={"Tiong Bahru Bakery": bakery}), FakeClient(reply=as_reply(planned))).stops
    assert (stops[0].place, stops[0].address, stops[0].place_id) == (
        "Tiong Bahru Bakery",
        "56 Eng Hoon St, #01-70, Singapore 160056",
        "tbb-1",
    )
    assert all(s.address == "" for s in stops[1:])  # not on Google: no address shown


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


def bad_request():
    return api_error(openai.BadRequestError, 400)


def test_asks_for_strict_structured_output():
    client = FakeClient()
    suggest_plan(REQUEST, client=client, link_checker=lambda it: it)
    call = client.calls[0]
    assert call["via"] == "parse" and call["response_format"] is PlannedItinerary


def test_truncated_reply_is_retried_once():
    client = FakeClient(replies=[truncated(), as_reply(GOOD)])
    assert suggest_plan(REQUEST, client=client, link_checker=lambda it: it) == GOOD
    assert len(client.calls) == 2


def test_gives_up_after_two_truncated_replies(caplog):
    client = FakeClient(replies=[truncated(), truncated()])
    with pytest.raises(PlanError, match="Couldn't make a plan this time"):
        suggest_plan(REQUEST, client=client, link_checker=lambda it: it)
    assert "length limit" in caplog.text


def test_refusal_becomes_try_again(caplog):
    with pytest.raises(PlanError, match="Couldn't make a plan this time"):
        suggest_plan(REQUEST, client=FakeClient(refusal="I can't help with that"), link_checker=lambda it: it)
    assert "I can't help with that" in caplog.text


def test_falls_back_to_free_text_when_schema_is_rejected(caplog):
    fenced = f"```json\n{as_reply(GOOD)}\n```"
    client = FakeClient(replies=[bad_request(), fenced])
    assert suggest_plan(REQUEST, client=client, link_checker=lambda it: it) == GOOD
    first, second = client.calls
    assert (first["via"], second["via"]) == ("parse", "create")
    assert "response_format" not in second
    assert second["messages"] == first["messages"] and second["max_tokens"] == first["max_tokens"]
    assert "falling back" in caplog.text.lower()


def test_fallback_reply_from_another_model_is_rejected():
    client = FakeClient(replies=[bad_request(), as_reply(GOOD)], model="kimi-k3")
    with pytest.raises(PlanError, match="not GLM-5.3 Flash"):
        suggest_plan(REQUEST, client=client, link_checker=lambda it: it)


def test_bad_request_in_fallback_too_becomes_try_again():
    client = FakeClient(replies=[bad_request(), bad_request()])
    with pytest.raises(PlanError, match="try again"):
        suggest_plan(REQUEST, client=client, link_checker=lambda it: it)
