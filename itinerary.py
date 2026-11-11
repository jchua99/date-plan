"""What a meet-up plan looks like, and the rules a good one follows."""

from datetime import date, time
from urllib.parse import urlencode

from pydantic import BaseModel, field_validator

MIN_STOPS = 3
MAX_STOPS = 5

# The first stop starts when the pair said they're free. Allow a little
# slack, e.g. a café that opens at 14:15 for a pair free from 14:00.
START_LEEWAY_MINUTES = 15
# The plan should last about 3-4 hours. We only know when each stop starts,
# not when the last one ends, so measure from the first stop's start to the
# last stop's start, and only reject spans that are clearly wrong.
SHORTEST_PLAN_MINUTES = 90
LONGEST_PLAN_MINUTES = 5 * 60

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
    start_from: str = ""  # optional: where the pair set off from, for the first leg

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
    # The model's best guess at whether the place takes bookings. Not checked.
    reservable: bool = False
    # The reservation page. Only kept once checked (links.py or reservations.py).
    booking_url: str = ""
    # The place's Google Maps page, from Google Places (reservations.py).
    maps_url: str = ""
    # True once reservations.py has confirmed `reservable` with Google or a real booking page.
    checked: bool = False
    # The place's address and id on Google Maps (reservations.py). Never from the planning model.
    address: str = ""
    place_id: str = ""

    @property
    def reservation_url(self) -> str:
        """Where the Reserve button goes: the booking page, else Google Maps, else a search."""
        if self.booking_url:
            return self.booking_url
        if self.maps_url:
            return self.maps_url
        return "https://www.google.com/search?" + urlencode({"q": f"{self.place} reservation"})


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


def _minutes(at: time) -> int:
    """Minutes since midnight."""
    return at.hour * 60 + at.minute


def check_itinerary(itinerary: Itinerary, request: PlanRequest) -> Itinerary:
    """Return the itinerary if it follows the rules, otherwise raise ItineraryError.

    Rules: 3-5 stops, every stop has an activity and a place, times are in
    order, nothing starts before the pair said they're free, the first stop
    starts at that time (give or take START_LEEWAY_MINUTES), the plan lasts
    roughly 3-4 hours (see SHORTEST_PLAN_MINUTES and LONGEST_PLAN_MINUTES),
    and if they picked a cuisine, some stop mentions it.
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

    first = parse_time(stops[0].time)
    if _minutes(first) - _minutes(request.start_time) > START_LEEWAY_MINUTES:
        raise ItineraryError(f"Stop 1 starts at {first:%H:%M}, not at {request.start_time:%H:%M}")
    span = _minutes(previous) - _minutes(first)
    if span < SHORTEST_PLAN_MINUTES:
        raise ItineraryError(f"The plan only lasts {span} minutes")
    if span > LONGEST_PLAN_MINUTES:
        raise ItineraryError(f"The plan lasts {span} minutes, far more than 3-4 hours")

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
