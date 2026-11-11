"""Ask GLM-5.3 Flash (through OpenCode) for a suggested meet-up plan."""

import json
import logging
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
from links import check_booking_links
import progress as stages
from reservations import ReservationHarness, check_places, is_model, place_name, places_key, use_google_details

logger = logging.getLogger(__name__)

MODEL = "glm-5.3-flash"
MODEL_LABEL = "GLM-5.3 Flash"
# Reasoning tokens count toward this, and the model can reason for 5,000+ tokens.
MAX_TOKENS = 16000
ATTEMPTS = 2
# How many times to ask for a new plan when one includes a closed place.
REPLANS = 2
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
place (just its name, with no address or directions: the address is looked up \
separately), a one-line note on why it helps them get to know each other, and \
"reservable": true if the place usually takes table or slot bookings (most \
sit-down restaurants and cafés, classes, workshops), otherwise false (hawker \
centres, parks, walks, shops). For a reservable place, also give \
"booking_url": the full https address of its online reservation page (its own \
site, or its page on a booking platform such as Chope, SevenRooms or \
TableCheck) if you know it; otherwise "". The first stop starts at the time \
they give. Give the plan a short, friendly title.

If they give a cuisine, one of the stops must be a meal at a real place that \
serves that cuisine, and that stop's activity must name the cuisine, for \
example "Malay lunch".

Reply with only a JSON object, no other text, in exactly this shape:
{{"title": "...", "stops": [{{"time": "14:00", "activity": "...", "place": "...", "note": "...", "reservable": false, "booking_url": ""}}]}}"""

TRY_AGAIN = "Couldn't make a plan this time. Please try again."


class PlanError(Exception):
    """Something went wrong. The message is safe to show the pair."""


def build_prompt(request: PlanRequest, avoid: list[str] = ()) -> str:
    prompt = (
        # Build the day number separately: "%-d" only works on macOS/Linux.
        f"Day: {request.day:%A} {request.day.day} {request.day:%B %Y}\n"
        f"Free from: {request.start_time:%H:%M}\n"
        f"Area: {request.area}"
    )
    if request.cuisine != NO_PREFERENCE:
        prompt += f"\nCuisine: {request.cuisine}"
    if avoid:
        prompt += "\nThese places have closed, so don't suggest them: " + "; ".join(avoid)
    return prompt


def read_itinerary(text: str) -> Itinerary:
    """Pull the JSON object out of the model's reply, even if it's wrapped in ``` fences."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise ValueError("no JSON object in reply")
    return Itinerary.model_validate_json(text[start : end + 1])


def suggest_plan(request: PlanRequest, client=None, link_checker=None, place_checker=None, progress=None) -> Itinerary:
    """Return a checked itinerary written by GLM-5.3 Flash, or raise PlanError.

    `place_checker(itinerary, area)` looks every stop up on Google Maps and
    returns one PlaceCheck per stop. By default it uses Google Places when
    GOOGLE_PLACES_API_KEY is set; without a key, places aren't checked.

    `progress(message)` is told what's happening before each slow step
    (see progress.py), so the page can show it.
    """
    progress = progress or stages.ignore
    if client is None:
        key = os.environ.get(KEY_NAME)
        if not key:
            raise PlanError(f"No OpenCode API key found. Add {KEY_NAME} to your .env file.")
        client = openai.OpenAI(api_key=key, base_url=BASE_URL)
    if place_checker is None:
        google_key = places_key()
        if google_key:
            place_checker = lambda it, area: check_places(it, area, google_key)  # noqa: E731

    # The model sometimes suggests places that have since closed. Ask again,
    # naming them, and as a last resort take them out of the plan.
    avoid: list[str] = []
    progress(stages.WRITING)
    for _ in range(REPLANS + 1):
        itinerary = _checked_plan(client, request, avoid)
        if place_checker is None:
            return _finish(itinerary, request, client, link_checker, progress)
        progress(stages.CHECKING)
        checks = place_checker(itinerary, request.area)
        closed = [c for c in checks if c.closed]
        if not closed:
            # Show Google's name and address for every stop, never the planner's.
            return _finish(use_google_details(itinerary, checks), request, client, link_checker, progress)
        for c in closed:
            logger.info("%s %s", c.place, c.closed)
        avoid += [c.place for c in closed if c.place not in avoid]
        logger.info("Asking again without closed places: %s", avoid)
        progress(stages.replacing([place_name(c.place) for c in closed]))

    closed_at = {c.index for c in closed}
    itinerary = use_google_details(itinerary, checks)
    still_open = [s for i, s in enumerate(itinerary.stops) if i not in closed_at]
    try:
        itinerary = check_itinerary(itinerary.model_copy(update={"stops": still_open}), request)
    except ItineraryError:
        raise PlanError("The plans suggested kept including places that have closed. Please try again.") from None
    return _finish(itinerary, request, client, link_checker, progress)


def _checked_plan(client, request: PlanRequest, avoid: list[str]) -> Itinerary:
    """Ask for a plan and check it follows the rules, or raise PlanError."""
    # GLM-5.3 Flash reasons before it answers, and its reasoning counts toward
    # max_tokens. Now and then it reasons for so long that no answer is left, so
    # allow plenty of room and ask once more if the reply can't be read.
    for attempt in range(1, ATTEMPTS + 1):
        itinerary = _ask_for_plan(client, request, avoid)
        if itinerary is not None:
            break
        logger.warning("Plan reply %d of %d couldn't be read", attempt, ATTEMPTS)
    else:
        raise PlanError(TRY_AGAIN)

    try:
        return check_itinerary(itinerary, request)
    except MissingCuisineError:
        raise PlanError(f"The suggested plan didn't include a {request.cuisine} meal. Please try again.") from None
    except ItineraryError:
        raise PlanError("The suggested plan didn't quite add up. Please try again.") from None


def _ask_for_plan(client, request: PlanRequest, avoid: list[str] = ()) -> Itinerary | None:
    """One request to the model. Returns None if the reply can't be read as a plan."""
    try:
        response = client.chat.completions.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_prompt(request, avoid)},
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
    if not is_model(response.model, MODEL):
        raise PlanError(f"The plan came from {response.model!r}, not {MODEL_LABEL}, so it wasn't used.")

    reply = response.choices[0].message.content if response.choices else None
    try:
        itinerary = read_itinerary(reply or "")
    except (ValueError, pydantic.ValidationError):
        return None
    # Only the Google checks may fill these in, never the planning model.
    unchecked = {"maps_url": "", "checked": False, "address": "", "place_id": ""}
    return itinerary.model_copy(update={"stops": [s.model_copy(update=unchecked) for s in itinerary.stops]})


def _finish(itinerary: Itinerary, request: PlanRequest, client, link_checker, progress=stages.ignore) -> Itinerary:
    """Check the plan's booking details before it is shown."""
    if any(stop.reservable for stop in itinerary.stops):
        progress(stages.BOOKING)
    if link_checker is not None:
        return link_checker(itinerary)
    key = places_key()
    if key:
        # Let the model look each bookable stop up on Google and the web, then check what it found.
        return ReservationHarness(client, MODEL, key).check_itinerary(itinerary, request.area)
    # No Google key: the planner's booking links are guesses, so keep only the ones that load.
    return check_booking_links(itinerary)
