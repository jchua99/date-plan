"""Ask GLM-5.3 Flash (through OpenCode) for a suggested meet-up plan."""

import json
import os

import openai
import pydantic

from itinerary import (
    MAX_STOPS,
    MIN_STOPS,
    NO_PREFERENCE,
    Itinerary,
    ItineraryError,
    MissingCuisineError,
    PlanRequest,
    check_itinerary,
)

MODEL = "glm-5.3-flash"
MODEL_LABEL = "GLM-5.3 Flash"
# OpenCode's OpenAI-compatible endpoint; the SDK adds /chat/completions.
BASE_URL = "https://opencode.ai/inference/openai/v1"
KEY_NAME = "OPENCODE_API_KEY"

SYSTEM_PROMPT = f"""\
You plan a next meet-up for two people in Singapore who met recently and are \
still getting to know each other.

Suggest {MIN_STOPS} to {MAX_STOPS} activities for one afternoon or evening, \
lasting about 3 to 4 hours in total. Mix something you do side by side (a walk, \
a class, a game) with somewhere quiet enough to talk. Keep it low-pressure and \
moderately priced, and use real places in or near the area they give, with \
realistic travel time between stops.

For each stop give: the start time as HH:MM in 24-hour time, the activity, the \
place, and a one-line note on why it helps them get to know each other. The \
first stop starts at the time they give. Give the plan a short, friendly title.

If they give a cuisine, one of the stops must be a meal at a real place that \
serves that cuisine, and that stop's activity must name the cuisine, for \
example "Malay lunch".

Reply with only a JSON object, no other text, in exactly this shape:
{{"title": "...", "stops": [{{"time": "14:00", "activity": "...", "place": "...", "note": "..."}}]}}"""

TRY_AGAIN = "Couldn't make a plan this time. Please try again."


class PlanError(Exception):
    """Something went wrong. The message is safe to show the pair."""


def build_prompt(request: PlanRequest) -> str:
    prompt = (
        # Build the day number separately: "%-d" only works on macOS/Linux.
        f"Day: {request.day:%A} {request.day.day} {request.day:%B %Y}\n"
        f"Free from: {request.start_time:%H:%M}\n"
        f"Area: {request.area}"
    )
    if request.cuisine != NO_PREFERENCE:
        prompt += f"\nCuisine: {request.cuisine}"
    return prompt


def read_itinerary(text: str) -> Itinerary:
    """Pull the JSON object out of the model's reply, even if it's wrapped in ``` fences."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise ValueError("no JSON object in reply")
    return Itinerary.model_validate_json(text[start : end + 1])


def suggest_plan(request: PlanRequest, client=None) -> Itinerary:
    """Return a checked itinerary written by GLM-5.3 Flash, or raise PlanError."""
    if client is None:
        key = os.environ.get(KEY_NAME)
        if not key:
            raise PlanError(f"No OpenCode API key found. Add {KEY_NAME} to your .env file.")
        client = openai.OpenAI(api_key=key, base_url=BASE_URL)

    try:
        response = client.chat.completions.create(
            model=MODEL,
            max_tokens=8000,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_prompt(request)},
            ],
        )
    except openai.AuthenticationError:
        raise PlanError("The OpenCode API key is missing or wrong. Check your .env file.") from None
    except openai.PermissionDeniedError:
        raise PlanError(
            f"OpenCode won't let this key use {MODEL_LABEL}. Turn on {MODEL_LABEL} under Models in your OpenCode console."
        ) from None
    except openai.RateLimitError:
        raise PlanError("Too many requests, or out of OpenCode credit. Wait a minute and try again.") from None
    except openai.APIConnectionError:
        raise PlanError("Couldn't reach OpenCode. Check your internet connection and try again.") from None
    except openai.OpenAIError:
        raise PlanError(TRY_AGAIN) from None

    # Only accept a plan that this model actually wrote.
    if MODEL not in (response.model or "").lower():
        raise PlanError(f"The plan came from {response.model!r}, not {MODEL_LABEL}, so it wasn't used.")

    reply = response.choices[0].message.content if response.choices else None
    try:
        itinerary = read_itinerary(reply or "")
    except (ValueError, pydantic.ValidationError):
        raise PlanError(TRY_AGAIN) from None

    try:
        return check_itinerary(itinerary, request)
    except MissingCuisineError:
        raise PlanError(f"The suggested plan didn't include a {request.cuisine} meal. Please try again.") from None
    except ItineraryError:
        raise PlanError("The suggested plan didn't quite add up. Please try again.") from None
