"""Public-transport travel between the stops of a plan, using Singapore's OneMap."""

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from itinerary import Itinerary, PlanRequest, parse_time

BASE_URL = "https://www.onemap.gov.sg"
TOKEN_NAME = "ONEMAP_API_TOKEN"

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


class OneMap:
    """A small client for OneMap's search and public-transport routing APIs."""

    def __init__(self, token: str):
        self.token = token

    def _get(self, path: str, params: dict) -> dict:
        request = Request(f"{BASE_URL}{path}?{urlencode(params)}", headers={"Authorization": self.token})
        try:
            with urlopen(request, timeout=15) as response:
                return json.loads(response.read())
        except HTTPError as problem:
            if problem.code in (401, 403):
                raise TravelError(TOKEN_EXPIRED) from None
            if problem.code == 429:
                raise TravelError("OneMap is busy. Wait a minute and try again.") from None
            raise
        except URLError:
            raise TravelError("Couldn't reach OneMap. Check your internet connection.") from None
        except ValueError:
            raise TravelError(NO_ROUTE) from None

    def find(self, text: str) -> Place | None:
        """Look a place up by name. Tries the part before any comma if the full name isn't found."""
        tries = [text]
        if "," in text:
            tries.append(text.split(",")[0].strip())
        for search in tries:
            try:
                reply = self._get(
                    "/api/common/elastic/search",
                    {"searchVal": search, "returnGeom": "Y", "getAddrDetails": "N", "pageNum": 1},
                )
            except HTTPError:
                return None
            results = reply.get("results") or []
            if results:
                return Place(text, float(results[0]["LATITUDE"]), float(results[0]["LONGITUDE"]))
        return None

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

    def find(self, text):
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


def _minus(at: time, minutes: int) -> time:
    earlier = datetime.combine(date.min, at) - timedelta(minutes=minutes)
    return earlier.time() if earlier.date() == date.min else time(0, 0)


class _Looker:
    """Does the lookups for one plan, and stops asking OneMap after it fails."""

    def __init__(self, client):
        self.client = client
        self.failure: str | None = None

    def find(self, text: str) -> Place | None:
        if self.failure:
            return None
        try:
            return self.client.find(text)
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


def plan_travel(itinerary: Itinerary, request: PlanRequest, client=None) -> Travel:
    """Work out public-transport travel for a plan. Never raises: problems go on the legs."""
    stops = itinerary.stops
    if client is None:
        token = os.environ.get(TOKEN_NAME, "").strip()
        client = OneMap(token) if token else _NoToken()

    looker = _Looker(client)
    places = [looker.find(stop.place) for stop in stops]

    start = first_leg = None
    start_text = request.start_from.strip()
    if start_text:
        start = looker.find(start_text)
        first_stop = stops[0]
        first_leg = looker.leg(start_text, start, first_stop.place, places[0], request.day,
                               _minus(parse_time(first_stop.time), 60), first_stop.time)

    legs = []
    for i, (here, there) in enumerate(zip(stops, stops[1:])):
        leg = looker.leg(here.place, places[i], there.place, places[i + 1], request.day,
                         parse_time(here.time), there.time)
        leg.tight = bool(leg.leave_by) and leg.leave_by < here.time
        legs.append(leg)

    return Travel(places=places, legs=legs, start=start, first_leg=first_leg)
