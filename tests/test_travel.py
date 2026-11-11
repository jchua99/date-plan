import json
from datetime import date, time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

import pytest

from itinerary import PlanRequest
from tests.test_planner import GOOD  # stops at 14:00, 15:00, 16:30
from travel import (
    NO_TOKEN,
    TOKEN_EXPIRED,
    OneMap,
    Place,
    TravelError,
    decode_polyline,
    describe_route,
    google_maps_link,
    leave_by,
    plan_travel,
    search_variants,
)

# A real OneMap reply (trimmed): walk to Havelock, TE line to Gardens by the Bay, walk.
ROUTE = json.loads((Path(__file__).parent / "fixtures" / "onemap_route.json").read_text())
HAVELOCK_TRIP = ROUTE["plan"]["itineraries"][0]


def leg(mode, minutes, route="", start="", end=""):
    return {"mode": mode, "route": route, "duration": minutes * 60, "from": {"name": start}, "to": {"name": end}}


# --- Describing a trip ---------------------------------------------------


def test_describes_real_mrt_trip():
    minutes, text = describe_route(HAVELOCK_TRIP)
    assert minutes == 37  # 2193 seconds, rounded
    assert text == "Walk 9 min → MRT TE line: Havelock to Gardens By The Bay → Walk 18 min"


def test_describes_bus_with_its_number():
    trip = {"duration": 900, "legs": [leg("WALK", 3), leg("BUS", 10, "123", "BLK 1", "UE SQ"), leg("WALK", 2)]}
    assert describe_route(trip) == (15, "Walk 3 min → Bus 123: Blk 1 to Ue Sq → Walk 2 min")


def test_leaves_out_walks_under_a_minute():
    trip = {"duration": 600, "legs": [leg("WALK", 0), leg("SUBWAY", 10, "NE", "FARRER PARK MRT STATION", "OUTRAM PARK MRT STATION")]}
    assert describe_route(trip)[1] == "MRT NE line: Farrer Park to Outram Park"


def test_walk_only_trip():
    assert describe_route({"duration": 480, "legs": [leg("WALK", 8)]}) == (8, "Walk 8 min")


def test_light_rail_is_called_lrt():
    trip = {"duration": 300, "legs": [leg("TRAM", 5, "BP", "CHOA CHU KANG LRT STATION", "SOUTH VIEW LRT STATION")]}
    assert describe_route(trip)[1] == "LRT BP line: Choa Chu Kang to South View"


# --- Leave-by time -------------------------------------------------------


def test_leave_by_is_next_stop_time_minus_trip():
    assert leave_by("15:00", 37) == "14:23"


def test_leave_by_crosses_the_hour():
    assert leave_by("10:05", 10) == "09:55"


def test_leave_by_never_goes_before_midnight():
    assert leave_by("00:10", 30) == "00:00"


# --- Google Maps link ----------------------------------------------------


def test_google_link_asks_for_public_transport():
    url = google_maps_link("Tiong Bahru Bakery", "Gardens by the Bay")
    parts = urlparse(url)
    query = parse_qs(parts.query)
    assert url.startswith("https://www.google.com/maps/dir/?")
    assert query == {
        "api": ["1"],
        "origin": ["Tiong Bahru Bakery, Singapore"],
        "destination": ["Gardens by the Bay, Singapore"],
        "travelmode": ["transit"],
    }


# --- Route line for the map ----------------------------------------------


def test_decodes_google_style_polyline():
    # The example from Google's polyline format docs.
    assert decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@") == [
        [38.5, -120.2],
        [40.7, -120.95],
        [43.252, -126.453],
    ]


def test_decodes_real_onemap_leg_inside_singapore():
    points = decode_polyline(HAVELOCK_TRIP["legs"][1]["legGeometry"]["points"])
    assert len(points) > 10
    assert all(1.1 < lat < 1.5 and 103.5 < lng < 104.1 for lat, lng in points)


# --- Putting it together: plan_travel -------------------------------------

REQUEST = PlanRequest(day=date(2026, 11, 14), start_time=time(14, 0), area="Tiong Bahru")
PLACES = {
    "Tiong Bahru Bakery": Place("Tiong Bahru Bakery", 1.2848, 103.8317),
    "Tiong Bahru estate": Place("Tiong Bahru estate", 1.2832, 103.8300),
    "BooksActually": Place("BooksActually", 1.2816, 103.8636),
    "Outram Park MRT": Place("Outram Park MRT", 1.2802, 103.8394),
}


class FakeOneMap:
    """Stands in for OneMap so tests run offline. Every trip is HAVELOCK_TRIP (37 min)."""

    def __init__(self, places=PLACES, trip=HAVELOCK_TRIP, error=None):
        self.places, self.trip, self.error = places, trip, error
        self.routes = []

    def find(self, text):
        if self.error:
            raise self.error
        return self.places.get(text)

    def route(self, start, end, day, at):
        if self.error:
            raise self.error
        self.routes.append((start.name, end.name, day, at))
        return self.trip


def test_one_leg_between_each_pair_of_stops():
    travel = plan_travel(GOOD, REQUEST, client=FakeOneMap())
    assert travel.first_leg is None
    assert [(leg.origin, leg.destination) for leg in travel.legs] == [
        ("Tiong Bahru Bakery", "Tiong Bahru estate"),
        ("Tiong Bahru estate", "BooksActually"),
    ]
    first = travel.legs[0]
    assert first.minutes == 37
    assert first.route.startswith("Walk 9 min → MRT TE line")
    assert first.leave_by == "14:23"  # next stop 15:00 minus 37 min
    assert first.google_url == google_maps_link("Tiong Bahru Bakery", "Tiong Bahru estate")
    assert first.path and first.problem is None


def test_tight_when_leave_by_is_before_the_stop_starts():
    long_trip = {**HAVELOCK_TRIP, "duration": 70 * 60}  # 70 min, but stops are 60 min apart
    travel = plan_travel(GOOD, REQUEST, client=FakeOneMap(trip=long_trip))
    assert travel.legs[0].leave_by == "13:50"
    assert travel.legs[0].tight  # 13:50 is before the 14:00 coffee starts
    assert not travel.legs[1].tight  # 16:30 - 70 min = 15:20, after 15:00


def test_asks_onemap_for_trips_on_the_plan_day_leaving_at_each_stop_time():
    client = FakeOneMap()
    plan_travel(GOOD, REQUEST, client=client)
    assert [(day, at) for _, _, day, at in client.routes] == [
        (date(2026, 11, 14), time(14, 0)),
        (date(2026, 11, 14), time(15, 0)),
    ]


def test_starting_point_adds_a_first_leg_to_stop_one():
    request = REQUEST.model_copy(update={"start_from": "Outram Park MRT"})
    client = FakeOneMap()
    travel = plan_travel(GOOD, request, client=client)
    first = travel.first_leg
    assert (first.origin, first.destination) == ("Outram Park MRT", "Tiong Bahru Bakery")
    assert first.leave_by == "13:23"
    assert not first.tight  # nothing before the first stop to clash with
    assert client.routes[0][3] == time(13, 0)  # looked up an hour before stop 1
    assert travel.start == PLACES["Outram Park MRT"]


def test_map_pins_are_the_stops_that_were_found():
    travel = plan_travel(GOOD, REQUEST, client=FakeOneMap())
    assert travel.places == [PLACES["Tiong Bahru Bakery"], PLACES["Tiong Bahru estate"], PLACES["BooksActually"]]
    assert travel.has_map


def test_map_data_numbers_pins_by_stop_and_skips_missing_ones():
    places = {k: v for k, v in PLACES.items() if k != "Tiong Bahru estate"}
    request = REQUEST.model_copy(update={"start_from": "Outram Park MRT"})
    data = plan_travel(GOOD, request, client=FakeOneMap(places=places)).map_data()
    assert [(pin["n"], pin["name"]) for pin in data["pins"]] == [(1, "Tiong Bahru Bakery"), (3, "BooksActually")]
    assert data["start"] == {"name": "Outram Park MRT", "lat": 1.2802, "lng": 103.8394}
    assert len(data["paths"]) == 1  # only the start -> stop 1 leg had a route


def test_place_not_found_only_affects_its_legs():
    places = {k: v for k, v in PLACES.items() if k != "Tiong Bahru estate"}
    travel = plan_travel(GOOD, REQUEST, client=FakeOneMap(places=places))
    assert travel.places[1] is None
    for leg in travel.legs:  # both legs touch the missing stop
        assert leg.minutes is None
        assert leg.problem == "Couldn't find Tiong Bahru estate on the map."
        assert leg.google_url  # the Google link is always there
    assert travel.has_map  # the other two stops still show


def test_no_route_found():
    travel = plan_travel(GOOD, REQUEST, client=FakeOneMap(trip=None))
    assert all(leg.problem == "Couldn't get travel time for this leg." for leg in travel.legs)


def test_no_places_found_hides_the_map():
    travel = plan_travel(GOOD, REQUEST, client=FakeOneMap(places={}))
    assert not travel.has_map


@pytest.mark.parametrize("message", [TOKEN_EXPIRED, "Couldn't reach OneMap. Check your internet connection."])
def test_onemap_problem_is_shown_on_every_leg_and_never_raised(message):
    travel = plan_travel(GOOD, REQUEST, client=FakeOneMap(error=TravelError(message)))
    assert [leg.problem for leg in travel.legs] == [message, message]
    assert all(leg.google_url for leg in travel.legs)
    assert not travel.has_map


def test_no_token_means_no_travel_but_still_links(monkeypatch):
    monkeypatch.delenv("ONEMAP_API_TOKEN", raising=False)
    travel = plan_travel(GOOD, REQUEST)
    assert [leg.problem for leg in travel.legs] == [NO_TOKEN, NO_TOKEN]
    assert all(leg.google_url for leg in travel.legs)


# --- The OneMap client itself (HTTP faked) ---------------------------------


class FakeResponse:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_urlopen(monkeypatch, body=None, error=None):
    seen = []

    def urlopen(request, timeout):
        seen.append(request)
        if error:
            raise error
        return FakeResponse(body)

    monkeypatch.setattr("travel.urlopen", urlopen)
    return seen


def test_find_returns_first_search_result_with_token(monkeypatch):
    seen = fake_urlopen(monkeypatch, {"found": 1, "results": [{"SEARCHVAL": "TIONG BAHRU BAKERY", "LATITUDE": "1.31", "LONGITUDE": "103.83"}]})
    place = OneMap("secret").find("Tiong Bahru Bakery")
    assert place == Place("Tiong Bahru Bakery", 1.31, 103.83)
    query = parse_qs(urlparse(seen[0].full_url).query)
    assert query["searchVal"] == ["Tiong Bahru Bakery"] and query["returnGeom"] == ["Y"]
    assert seen[0].get_header("Authorization") == "secret"


def test_find_tries_the_name_before_the_comma(monkeypatch):
    replies = iter([{"found": 0, "results": []}, {"found": 1, "results": [{"LATITUDE": "1.3", "LONGITUDE": "103.8"}]}])
    seen = []

    def urlopen(request, timeout):
        seen.append(parse_qs(urlparse(request.full_url).query)["searchVal"][0])
        return FakeResponse(next(replies))

    monkeypatch.setattr("travel.urlopen", urlopen)
    assert OneMap("t").find("Tiong Bahru Market, Seng Poh Road") == Place("Tiong Bahru Market, Seng Poh Road", 1.3, 103.8)
    assert seen == ["Tiong Bahru Market, Seng Poh Road", "Tiong Bahru Market"]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Outram Park MRT", ["Outram Park MRT"]),
        # Real names the model wrote that OneMap couldn't find as-is:
        ("BooksActually, 115 Yong Siak Street", ["BooksActually, 115 Yong Siak Street", "BooksActually", "115 Yong Siak Street", "Yong Siak Street"]),
        ("Tiong Bahru estate & Seng Poh Garden", ["Tiong Bahru estate & Seng Poh Garden", "Tiong Bahru estate", "Seng Poh Garden"]),
        (
            "Tiong Bahru Market & Food Centre, 30 Seng Poh Road",
            ["Tiong Bahru Market & Food Centre, 30 Seng Poh Road", "Tiong Bahru Market & Food Centre", "30 Seng Poh Road",
             "Tiong Bahru Market", "Food Centre", "Seng Poh Road"],
        ),
        ("Gardens by the Bay and Marina Barrage", ["Gardens by the Bay and Marina Barrage", "Gardens by the Bay", "Marina Barrage"]),
    ],
)
def test_search_variants_go_from_exact_to_looser(text, expected):
    assert search_variants(text) == expected


def test_find_nothing(monkeypatch):
    fake_urlopen(monkeypatch, {"found": 0, "results": []})
    assert OneMap("t").find("Nowhere") is None


def test_route_asks_for_public_transport_and_returns_first_trip(monkeypatch):
    seen = fake_urlopen(monkeypatch, ROUTE)
    trip = OneMap("t").route(PLACES["Tiong Bahru Bakery"], PLACES["BooksActually"], date(2026, 11, 14), time(15, 0))
    assert trip == HAVELOCK_TRIP
    query = parse_qs(urlparse(seen[0].full_url).query)
    assert query["routeType"] == ["pt"] and query["mode"] == ["TRANSIT"]
    assert query["date"] == ["11-14-2026"] and query["time"] == ["15:00:00"]
    assert query["start"] == ["1.2848,103.8317"] and query["end"] == ["1.2816,103.8636"]


def http_error(status):
    return HTTPError("https://www.onemap.gov.sg", status, "boom", {}, None)


def test_expired_token(monkeypatch):
    fake_urlopen(monkeypatch, error=http_error(401))
    with pytest.raises(TravelError, match="expired"):
        OneMap("old").find("Somewhere")


def test_no_route_is_none_not_an_error(monkeypatch):
    fake_urlopen(monkeypatch, error=http_error(404))
    assert OneMap("t").route(PLACES["Tiong Bahru Bakery"], PLACES["BooksActually"], date(2026, 11, 14), time(15, 0)) is None


def test_cannot_reach_onemap(monkeypatch):
    fake_urlopen(monkeypatch, error=URLError("offline"))
    with pytest.raises(TravelError, match="Couldn't reach OneMap"):
        OneMap("t").find("Somewhere")
