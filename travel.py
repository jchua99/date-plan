"""Public-transport travel between the stops of a plan, using Singapore's OneMap."""

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fetching import read_with_deadline
from itinerary import Itinerary, PlanRequest, parse_time

BASE_URL = "https://www.onemap.gov.sg"
TOKEN_NAME = "ONEMAP_API_TOKEN"
TIMEOUT_SECONDS = 15  # for each wait for data
DEADLINE_SECONDS = 20  # for a whole reply, however slowly it arrives
MAX_REPLY_BYTES = 5_000_000  # a route reply with its map lines is well under this
TOO_SLOW = "OneMap is taking too long to answer. Try again later."

NO_TOKEN = f"Add {TOKEN_NAME} to your .env file to see travel times."
TOKEN_EXPIRED = f"The OneMap token has expired (they last 3 days). Paste a new {TOKEN_NAME} into your .env file."
NO_ROUTE = "Couldn't get travel time for this leg."

MODE_NAMES = {"SUBWAY": "MRT", "TRAM": "LRT", "BUS": "Bus"}


def tidy_stop_name(name: str) -> str:
    """'HAVELOCK MRT STATION' -> 'Havelock'."""
    for suffix in (" MRT STATION", " LRT STATION"):
        if name.upper().endswith(suffix):
            name = name[: -len(suffix)]
    return name.title()


def describe_route(trip: dict) -> tuple[int, str]:
    """Turn one OneMap itinerary into (total minutes, one-line route).

    e.g. (37, "Walk 9 min → MRT TE line: Havelock to Gardens By The Bay → Walk 18 min")
    """
    parts = []
    for leg in trip["legs"]:
        mode = leg["mode"]
        minutes = round(leg["duration"] / 60)
        if mode == "WALK":
            if minutes >= 1:
                parts.append(f"Walk {minutes} min")
            continue
        start, end = tidy_stop_name(leg["from"]["name"]), tidy_stop_name(leg["to"]["name"])
        name = MODE_NAMES.get(mode, mode.title())
        if mode == "BUS":
            parts.append(f"Bus {leg['route']}: {start} to {end}")
        else:
            parts.append(f"{name} {leg['route']} line: {start} to {end}")
    return round(trip["duration"] / 60), " → ".join(parts)


def leave_by(next_stop_time: str, trip_minutes: int) -> str:
    """When to set off to reach the next stop on time, as HH:MM (never before midnight)."""
    arrive = parse_time(next_stop_time)
    minutes = max(arrive.hour * 60 + arrive.minute - trip_minutes, 0)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def google_maps_link(origin: str, destination: str) -> str:
    """A Google Maps public-transport directions link. Needs no API key."""
    query = {
        "api": "1",
        "origin": f"{origin}, Singapore",
        "destination": f"{destination}, Singapore",
        "travelmode": "transit",
    }
    return "https://www.google.com/maps/dir/?" + urlencode(query)


def decode_polyline(points: str) -> list[list[float]]:
    """Decode a Google-style encoded polyline into [[lat, lng], ...] for the map."""
    coordinates, index, lat, lng = [], 0, 0, 0
    while index < len(points):
        for axis in (0, 1):
            shift = result = 0
            while True:
                byte = ord(points[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if axis == 0:
                lat += delta
            else:
                lng += delta
        coordinates.append([round(lat / 1e5, 5), round(lng / 1e5, 5)])
    return coordinates


# --- Talking to OneMap -----------------------------------------------------


class TravelError(Exception):
    """OneMap couldn't help. The message is safe to show the pair."""


@dataclass
class Place:
    name: str  # as the plan wrote it
    lat: float
    lng: float


def search_variants(text: str) -> list[str]:
    """Names to try in OneMap, from exact to looser.

    The model writes things like "BooksActually, 115 Yong Siak Street" or
    "Tiong Bahru estate & Seng Poh Garden", which OneMap can't find whole.
    So also try each comma part, each half of "A & B" / "A and B" / "A to B", and
    addresses without the house number. Asides in brackets are dropped.
    """
    text = re.sub(r"\s*\([^)]*\)", "", text)  # drop asides like "(5-min walk)"
    parts = [part.strip() for part in text.split(",") if part.strip()]
    pieces = [piece.strip() for part in parts for piece in re.split(r"\s+(?:&|and|to)\s+", part) if piece.strip()]
    streets = [re.sub(r"^\d+[A-Za-z]?\s+", "", piece) for piece in pieces]
    variants = []
    for name in [text.strip(), *parts, *pieces, *streets]:
        if name and name not in variants:
            variants.append(name)
    return variants


NEARBY_KM = 1.5  # a match further than this from the area is probably another branch


def km_between(a: Place, b: Place) -> float:
    """Rough distance; fine at Singapore's latitude."""
    return ((a.lat - b.lat) ** 2 + (a.lng - b.lng) ** 2) ** 0.5 * 111


class OneMap:
    """A small client for OneMap's search and public-transport routing APIs."""

    def __init__(self, token: str):
        self.token = token

    def _get(self, path: str, params: dict) -> dict:
        request = Request(f"{BASE_URL}{path}?{urlencode(params)}", headers={"Authorization": self.token})
        try:
            with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                return json.loads(read_with_deadline(response, MAX_REPLY_BYTES, DEADLINE_SECONDS))
        except HTTPError as problem:
            if problem.code in (401, 403):
                raise TravelError(TOKEN_EXPIRED) from None
            if problem.code == 429:
                raise TravelError("OneMap is busy. Wait a minute and try again.") from None
            raise
        except URLError:
            raise TravelError("Couldn't reach OneMap. Check your internet connection.") from None
        except TimeoutError:
            raise TravelError(TOO_SLOW) from None
        except ValueError:
            raise TravelError(NO_ROUTE) from None

    def find(self, text: str, near: Place | None = None) -> Place | None:
        """Look a place up by name, trying looser versions of it if the full name isn't found.

        Chains like Tiong Bahru Bakery have several branches, so pick the result nearest `near`.
        If even that is far away, try the looser names (e.g. just the street) before settling for it.
        """
        fallback = None
        for search in search_variants(text):
            try:
                reply = self._get(
                    "/api/common/elastic/search",
                    {"searchVal": search, "returnGeom": "Y", "getAddrDetails": "N", "pageNum": 1},
                )
            except HTTPError:
                return None
            results = reply.get("results") or []
            found = [Place(text, float(r["LATITUDE"]), float(r["LONGITUDE"])) for r in results]
            if not found:
                continue
            if near is None:
                return found[0]
            best = min(found, key=lambda place: km_between(place, near))
            if km_between(best, near) <= NEARBY_KM:
                return best
            fallback = fallback or best
        return fallback

    def route(self, start: Place, end: Place, day: date, at: time) -> dict | None:
        """The quickest public-transport trip leaving at `at`, or None if there isn't one."""
        params = {
            "start": f"{start.lat},{start.lng}",
            "end": f"{end.lat},{end.lng}",
            "routeType": "pt",
            "mode": "TRANSIT",
            "date": f"{day:%m-%d-%Y}",
            "time": f"{at:%H:%M:%S}",
            "numItineraries": 1,
        }
        try:
            reply = self._get("/api/public/routingsvc/route", params)
        except HTTPError:
            return None  # e.g. 404 "No route found"
        trips = (reply.get("plan") or {}).get("itineraries") or []
        return trips[0] if trips else None


class _NoToken:
    """Used when there's no OneMap token: every lookup fails with a hint to add one."""

    def find(self, text, near=None):
        raise TravelError(NO_TOKEN)

    def route(self, start, end, day, at):
        raise TravelError(NO_TOKEN)


# --- Travel for a whole plan -----------------------------------------------


@dataclass
class Leg:
    """Getting from one place to the next. Shown under a stop."""

    origin: str
    destination: str
    google_url: str
    minutes: int | None = None
    route: str = ""
    leave_by: str = ""
    tight: bool = False  # leave-by is before the current stop even starts
    path: list = field(default_factory=list)  # [[lat, lng], ...] for the map
    problem: str | None = None


@dataclass
class Travel:
    places: list  # a Place (or None if not found) for each stop, in order
    legs: list  # legs[i] goes from stop i+1 to stop i+2
    start: Place | None = None
    first_leg: Leg | None = None

    @property
    def has_map(self) -> bool:
        return any(self.places) or self.start is not None

    def map_data(self) -> dict:
        """What the page's map draws: numbered pins, the starting point and the route lines."""
        legs = ([self.first_leg] if self.first_leg else []) + self.legs
        return {
            "pins": [
                {"n": number, "name": place.name, "lat": place.lat, "lng": place.lng}
                for number, place in enumerate(self.places, start=1)
                if place
            ],
            "start": {"name": self.start.name, "lat": self.start.lat, "lng": self.start.lng} if self.start else None,
            "paths": [leg.path for leg in legs if leg.path],
        }


def _minus(at: time, minutes: int) -> time:
    earlier = datetime.combine(date.min, at) - timedelta(minutes=minutes)
    return earlier.time() if earlier.date() == date.min else time(0, 0)


class _Looker:
    """Does the lookups for one plan, and stops asking OneMap after it fails."""

    def __init__(self, client):
        self.client = client
        self.failure: str | None = None

    def find(self, text: str, near: Place | None = None) -> Place | None:
        if self.failure:
            return None
        try:
            return self.client.find(text, near)
        except TravelError as problem:
            self.failure = str(problem)
            return None

    def leg(self, origin: str, start: Place | None, destination: str, end: Place | None,
            day: date, leave_at: time, arrive_at: str) -> Leg:
        leg = Leg(origin, destination, google_maps_link(origin, destination))
        if self.failure:
            leg.problem = self.failure
            return leg
        missing = origin if start is None else destination if end is None else None
        if missing:
            leg.problem = f"Couldn't find {missing} on the map."
            return leg
        try:
            trip = self.client.route(start, end, day, leave_at)
        except TravelError as problem:
            self.failure = leg.problem = str(problem)
            return leg
        if trip is None:
            leg.problem = NO_ROUTE
            return leg
        leg.minutes, leg.route = describe_route(trip)
        leg.leave_by = leave_by(arrive_at, leg.minutes)
        leg.path = [point for part in trip["legs"] for point in decode_polyline(part.get("legGeometry", {}).get("points", ""))]
        return leg


def postal_code(address: str) -> str:
    """The 6-digit Singapore postal code at the end of an address, or ''."""
    found = re.findall(r"\b(\d{6})\b", address or "")
    return found[-1] if found else ""


def _locate(looker: _Looker, stop, near: Place | None) -> Place | None:
    """Find a stop on OneMap.

    A stop checked on Google Maps (reservations.py) has Google's address. Its
    postal code is one building, so look that up first: the route then goes to
    the same place the page shows, not whichever branch OneMap finds nearest.
    """
    code = postal_code(getattr(stop, "address", ""))
    if code:
        found = looker.find(code)
        if found:
            return Place(stop.place, found.lat, found.lng)
    return looker.find(stop.place, near=near)


def _where(stop) -> str:
    """What to ask Google Maps for directions to: the address when it's known."""
    address = getattr(stop, "address", "")
    return f"{stop.place}, {address}" if address else stop.place


def plan_travel(itinerary: Itinerary, request: PlanRequest, client=None) -> Travel:
    """Work out public-transport travel for a plan. Never raises: problems go on the legs."""
    stops = itinerary.stops
    if client is None:
        token = os.environ.get(TOKEN_NAME, "").strip()
        client = OneMap(token) if token else _NoToken()

    looker = _Looker(client)
    area = looker.find(request.area)
    places = [_locate(looker, stop, area) for stop in stops]

    start = first_leg = None
    start_text = request.start_from.strip()
    if start_text:
        start = looker.find(start_text, near=area)
        first_stop = stops[0]
        first_leg = looker.leg(start_text, start, first_stop.place, places[0], request.day,
                               _minus(parse_time(first_stop.time), 60), first_stop.time)
        first_leg.google_url = google_maps_link(start_text, _where(first_stop))

    legs = []
    for i, (here, there) in enumerate(zip(stops, stops[1:])):
        leg = looker.leg(here.place, places[i], there.place, places[i + 1], request.day,
                         parse_time(here.time), there.time)
        leg.tight = bool(leg.leave_by) and leg.leave_by < here.time
        leg.google_url = google_maps_link(_where(here), _where(there))
        legs.append(leg)

    return Travel(places=places, legs=legs, start=start, first_leg=first_leg)
