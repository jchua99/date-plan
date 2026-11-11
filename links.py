"""Check the reservation links the model suggests before showing them.

The model has no web access, so a booking_url is a guess from memory and may
be made up or dead. Each one is fetched once; any that doesn't load is
dropped, and the Reserve button falls back to a search for the place instead.
"""

import ipaddress
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from itinerary import Itinerary

TIMEOUT_SECONDS = 4
USER_AGENT = "Mozilla/5.0 (compatible; date-plan link check)"


def looks_safe(url: str) -> bool:
    """Only plain https links to a public-looking host name: no IPs, ports, logins or localhost."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or port not in (None, 443) or parts.username or parts.password:
        return False
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")) or "." not in host:
        return False
    try:
        ipaddress.ip_address(host.strip("[]"))
        return False
    except ValueError:
        return True


def page_loads(url: str) -> bool:
    """True if the page answers with a success (after redirects).

    Only the status is needed, so the body is never read: a slow page can't
    keep this waiting while it trickles in (see fetching.py).
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def check_booking_links(itinerary: Itinerary, loads=page_loads) -> Itinerary:
    """Return the itinerary with every booking_url that is unsafe or doesn't load cleared."""
    stops = itinerary.stops
    to_check = [i for i, stop in enumerate(stops) if stop.reservable and looks_safe(stop.booking_url)]
    with ThreadPoolExecutor(max_workers=max(1, len(to_check))) as pool:
        ok = dict(zip(to_check, pool.map(lambda i: loads(stops[i].booking_url.strip()), to_check)))

    checked = [
        stop.model_copy(update={"booking_url": stop.booking_url.strip() if ok.get(i) else ""})
        for i, stop in enumerate(stops)
    ]
    return itinerary.model_copy(update={"stops": checked})
