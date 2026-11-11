"""What a meet-up plan looks like, and the rules a good one follows."""

import re
from datetime import date, time
from urllib.parse import urlencode

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


# Hours and minutes split by ":" or ".", maybe seconds, maybe am/pm:
# "14:30", "9:30", "9.30", "14:30:00", "7:30pm", "7:30 a.m.".
TIME_PATTERN = re.compile(r"(\d{1,2})[:.](\d{1,2})(?::\d{2})?\s*(?:([ap])\.?m\.?)?", re.IGNORECASE)


def parse_time(text: str) -> time:
    """Read a stop's time. Takes the small variations the model sometimes writes.

    Seconds are dropped, and "7:30pm" means 19:30. Anything else raises
    ItineraryError.
    """
    found = TIME_PATTERN.fullmatch(text.strip())
    try:
        if not found:
            raise ValueError
        hours, minutes, half = int(found[1]), int(found[2]), (found[3] or "").lower()
        if half:
            if not 1 <= hours <= 12:
                raise ValueError
            hours = hours % 12 + (12 if half == "p" else 0)
        return time(hours, minutes)
    except ValueError:
        raise ItineraryError(f"'{text}' is not a time like 14:30") from None


def check_itinerary(itinerary: Itinerary, request: PlanRequest) -> Itinerary:
    """Return the itinerary if it follows the rules, otherwise raise ItineraryError.

    Rules: 3-5 stops, every stop has an activity and a place, times are in
    order, nothing starts before the pair said they're free, and if they
    picked a cuisine, some stop mentions it. Each stop's time is rewritten
    as HH:MM.
    """
    stops = itinerary.stops
    if not MIN_STOPS <= len(stops) <= MAX_STOPS:
        raise ItineraryError(f"Needs {MIN_STOPS}-{MAX_STOPS} stops, got {len(stops)}")

    previous = None
    for number, stop in enumerate(stops, start=1):
        if not stop.activity.strip() or not stop.place.strip():
            raise ItineraryError(f"Stop {number} is missing an activity or a place")
        when = parse_time(stop.time)
        stop.time = f"{when:%H:%M}"  # store as HH:MM, so "9:00" shows as "09:00"
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
