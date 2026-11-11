import pytest

from itinerary import Itinerary, Stop
from reservations import PlaceCheck, check_places, look_up_place, place_name, same_name, use_google_details

PAPIS = {
    "id": "papis-tp",
    "displayName": {"text": "Papi's Tacos"},
    "businessStatus": "OPERATIONAL",
    "formattedAddress": "33 Tg Pagar Rd, Singapore 088456",
    "googleMapsUri": "https://maps.google.com/?cid=1",
}


def listing(name, status="OPERATIONAL", address="Singapore"):
    return {"id": name, "displayName": {"text": name}, "formattedAddress": address, "businessStatus": status}


def google(results_by_query):
    """Fake Places search: answers by query, records each query."""
    queries = []

    def search(query, key, fields=None, size=3):
        queries.append(query)
        return results_by_query.get(query, [])

    search.queries = queries
    return search


def stop(place, **extra):
    return Stop(time="19:00", activity="Dinner", place=place, note="n", **extra)


# --- Matching names ---


@pytest.mark.parametrize(
    "a, b",
    [
        ("Muchacho, 60 Tras Street", "Muchachos"),
        ("PS.Cafe Harding", "PS.Cafe Harding Road"),
        ("Tiong Bahru Market & Food Centre", "Tiong Bahru Market"),
        ("Bistro du Vin", "Bistro Du Vin"),
        ("Papi's Tacos, 105 Amoy Street", "Papi's Tacos"),
    ],
)
def test_same_place_names_match(a, b):
    assert same_name(a, b)


@pytest.mark.parametrize("a, b", [("Muchacho", "Hamamoto"), ("Over", "Overeasy Grill"), ("Singapore", "Singapore Zoo"), ("", "x")])
def test_different_names_do_not_match(a, b):
    assert not same_name(a, b)


@pytest.mark.parametrize(
    "written, name",
    [
        ("Papi's Tacos, 105 Amoy Street", "Papi's Tacos"),
        ("Papi's Tacos (5 Teck Lim Rd, ~10 min walk from Pinnacle@Duxton)", "Papi's Tacos"),
        ("Pinnacle@Duxton Skybridge (1 Cantonment Rd, S$6 ticket)", "Pinnacle@Duxton Skybridge"),
        ("Ann Siang Hill Park & Club Street", "Ann Siang Hill Park & Club Street"),
        ("(Unnamed spot)", "(Unnamed spot)"),
    ],
)
def test_place_name_drops_the_planners_address_and_directions(written, name):
    assert place_name(written) == name


# --- Looking a place up ---


def test_muchacho_is_found_closed_by_name_when_address_search_misses_it():
    # What Google really returned: the area search finds only neighbours, the name search finds it closed.
    search = google({
        "Muchacho Tanjong Pagar Singapore": [listing("Hamamoto"), listing("over")],
        "Muchacho Singapore": [listing("Muchachos", "CLOSED_PERMANENTLY", "182 Cecil St")],
    })
    assert look_up_place("Muchacho, 60 Tras Street", "Tanjong Pagar", "k", search) == ("has closed for good", None)
    # A one-word name before the comma might be cut short, so the whole name is tried first.
    assert search.queries == [
        "Muchacho, 60 Tras Street Tanjong Pagar Singapore",
        "Muchacho Tanjong Pagar Singapore",
        "Muchacho Singapore",
    ]


HELLO_KITTY = {**listing("Hello, Kitty Cafe", address="1 Orchard Rd"), "id": "hk"}


def test_name_with_a_comma_is_found_by_its_whole_name():
    search = google({
        "Hello, Kitty Cafe Orchard Singapore": [HELLO_KITTY],
        "Hello Orchard Singapore": [listing("Hello", address="9 Somewhere Else")],
    })
    assert look_up_place("Hello, Kitty Cafe", "Orchard", "k", search) == ("", HELLO_KITTY)
    assert search.queries == ["Hello, Kitty Cafe Orchard Singapore"]


def test_name_with_a_comma_and_bracketed_aside_is_found_by_its_whole_name():
    search = google({"Hello, Kitty Cafe Orchard Singapore": [HELLO_KITTY]})
    assert look_up_place("Hello, Kitty Cafe (5 min walk)", "Orchard", "k", search) == ("", HELLO_KITTY)


def test_address_after_the_comma_is_still_cut_off_without_an_extra_search():
    search = google({"Papi's Tacos Tanjong Pagar Singapore": [PAPIS]})
    assert look_up_place("Papi's Tacos, 105 Amoy Street", "Tanjong Pagar", "k", search) == ("", PAPIS)
    assert search.queries == ["Papi's Tacos Tanjong Pagar Singapore"]


def test_open_listing_is_returned_ignoring_the_planners_address():
    search = google({"Papi's Tacos Tanjong Pagar Singapore": [listing("Hamamoto"), PAPIS]})
    assert look_up_place("Papi's Tacos, 105 Amoy Street", "Tanjong Pagar", "k", search) == ("", PAPIS)


def test_open_listing_with_same_name_wins_over_closed_one():
    search = google({"PS.Cafe Dempsey Singapore": [listing("PS.Cafe", "CLOSED_PERMANENTLY"), listing("PS.Cafe Harding Road")]})
    closed, found = look_up_place("PS.Cafe", "Dempsey", "k", search)
    assert closed == "" and found["displayName"]["text"] == "PS.Cafe Harding Road"


def test_temporarily_closed_counts_as_closed():
    search = google({"Atlas Bugis Singapore": [listing("Atlas", "CLOSED_TEMPORARILY")]})
    assert look_up_place("Atlas", "Bugis", "k", search) == ("is closed for now", None)


def test_place_google_does_not_know_is_not_found_and_not_closed():
    assert look_up_place("Seng Poh Lane murals", "Tiong Bahru", "k", google({})) == ("", None)


def test_closed_neighbour_with_different_name_is_ignored():
    search = google({"Hamamoto Tanjong Pagar Singapore": [listing("Muchachos", "CLOSED_PERMANENTLY"), listing("Hamamoto")]})
    assert look_up_place("Hamamoto", "Tanjong Pagar", "k", search)[1]["displayName"]["text"] == "Hamamoto"


def test_check_places_checks_every_stop_and_survives_lookup_errors():
    stops = [stop("Duxton Hill"), stop("Muchacho, 60 Tras Street"), stop("Broken Lookup Bar")]

    def search(query, key, fields=None, size=3):
        if query.startswith("Broken"):
            raise OSError("timeout")
        if query.startswith("Muchacho"):
            return [listing("Muchachos", "CLOSED_PERMANENTLY")]
        return [listing("Duxton Hill")]

    checks = check_places(Itinerary(title="T", stops=stops), "Tanjong Pagar", "k", search)
    assert [(c.index, c.closed, bool(c.listing)) for c in checks] == [
        (0, "", True),
        (1, "has closed for good", False),
        (2, "", False),
    ]


# --- Showing Google's details ---


def test_google_name_and_address_replace_the_planners():
    plan = Itinerary(title="T", stops=[stop("Papi's Tacos, 105 Amoy Street")])
    shown = use_google_details(plan, [PlaceCheck(0, "x", listing=PAPIS)]).stops[0]
    assert shown.place == "Papi's Tacos"
    assert shown.address == "33 Tg Pagar Rd, Singapore 088456"
    assert shown.place_id == "papis-tp" and shown.maps_url == "https://maps.google.com/?cid=1"


def test_unchecked_address_is_dropped_when_google_does_not_know_the_place():
    plan = Itinerary(title="T", stops=[stop("Duxton Hill Park & Craig Road, Tanjong Pagar")])
    shown = use_google_details(plan, [PlaceCheck(0, "x")]).stops[0]
    assert shown.place == "Duxton Hill Park & Craig Road" and shown.address == "" and shown.place_id == ""


def test_unsafe_maps_link_from_google_is_not_used():
    plan = Itinerary(title="T", stops=[stop("Papi's Tacos")])
    unsafe = {**PAPIS, "googleMapsUri": "javascript:alert(1)"}
    shown = use_google_details(plan, [PlaceCheck(0, "x", listing=unsafe)]).stops[0]
    assert shown.maps_url == "" and shown.address
