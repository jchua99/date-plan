import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from travel import decode_polyline, describe_route, google_maps_link, leave_by

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
