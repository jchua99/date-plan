"""Public-transport travel between the stops of a plan, using Singapore's OneMap."""

from urllib.parse import urlencode

from itinerary import parse_time

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
