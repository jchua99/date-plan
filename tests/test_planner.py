from datetime import date, time
from types import SimpleNamespace

import httpx2
import openai
import pytest

from itinerary import Itinerary, PlanRequest, Stop
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
