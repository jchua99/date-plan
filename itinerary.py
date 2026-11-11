"""What a meet-up plan looks like, and the rules a good one follows."""

from datetime import date, time

from pydantic import BaseModel, field_validator

MIN_STOPS = 3
MAX_STOPS = 5

NO_PREFERENCE = "No preference"
CUISINES = (
    NO_PREFERENCE,
    "Chinese",
    "Malay",
    "Indian",
    "Peranakan",
    "Japanese",
    "Korean",
    "Thai",
    "Western",
    "Mexican",
    "Italian",
)


class PlanRequest(BaseModel):
    """What the pair tell the planner."""

    day: date
    start_time: time
    area: str
    cuisine: str = NO_PREFERENCE

    @field_validator("cuisine")
    @classmethod
    def cuisine_from_the_list(cls, value: str) -> str:
        if value not in CUISINES:
            raise ValueError(f"'{value}' is not one of the cuisines on offer")
        return value


class Stop(BaseModel):
    """One activity in the plan."""

    time: str  # "HH:MM", 24-hour
    activity: str
    place: str
    note: str


class Itinerary(BaseModel):
    """The shape the model must reply in."""

    title: str
    stops: list[Stop]


class ItineraryError(ValueError):
    """The itinerary breaks one of the rules below."""


class MissingCuisineError(ItineraryError):
    """The pair picked a cuisine, but no stop mentions it."""


def parse_time(text: str) -> time:
    try:
        hours, minutes = text.strip().split(":")
        return time(int(hours), int(minutes))
    except ValueError:
        raise ItineraryError(f"'{text}' is not a time like 14:30") from None


def check_itinerary(itinerary: Itinerary, request: PlanRequest) -> Itinerary:
    """Return the itinerary if it follows the rules, otherwise raise ItineraryError.

    Rules: 3-5 stops, every stop has an activity and a place, times are in
    order, nothing starts before the pair said they're free, and if they
    picked a cuisine, some stop mentions it.
    """
    stops = itinerary.stops
    if not MIN_STOPS <= len(stops) <= MAX_STOPS:
        raise ItineraryError(f"Needs {MIN_STOPS}-{MAX_STOPS} stops, got {len(stops)}")

    previous = None
    for number, stop in enumerate(stops, start=1):
        if not stop.activity.strip() or not stop.place.strip():
            raise ItineraryError(f"Stop {number} is missing an activity or a place")
        when = parse_time(stop.time)
        if when < request.start_time:
            raise ItineraryError(f"Stop {number} starts before {request.start_time:%H:%M}")
        if previous is not None and when <= previous:
            raise ItineraryError(f"Stop {number} isn't later than the stop before it")
        previous = when

    if request.cuisine != NO_PREFERENCE and not mentions_cuisine(itinerary, request.cuisine):
        raise MissingCuisineError(f"No stop mentions {request.cuisine} food")

    return itinerary


def mentions_cuisine(itinerary: Itinerary, cuisine: str) -> bool:
    """A loose check: does the cuisine's name appear in any stop's activity, place or note?

    It can miss a real match (e.g. "Din Tai Fung" without the word "Chinese"),
    which is why the prompt asks the model to name the cuisine.
    """
    words = " ".join(f"{s.activity} {s.place} {s.note}" for s in itinerary.stops)
    return cuisine.lower() in words.lower()
