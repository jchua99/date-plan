import time
from email.message import Message

import pytest

from fetching import read_with_deadline


class SlowResponse:
    """A reply that trickles in one byte at a time, like a stalling server."""

    def __init__(self, total=1000, pause=0.02, content_type="text/html"):
        self.left, self.pause = total, pause
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.reads = 0

    def read1(self, n=-1):
        self.reads += 1
        time.sleep(self.pause)
        if self.left <= 0:
            return b""
        self.left -= 1
        return b"x"

    def read(self, n=-1):
        raise AssertionError("read() waits for a full piece; read1() should be used")

    def geturl(self):
        return "https://slow.example/"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class PlainResponse:
    """A reply without read1, handed out in fixed pieces."""

    def __init__(self, body: bytes, piece=4):
        self.body, self.piece = body, piece

    def read(self, n=-1):
        n = min(n, self.piece) if n >= 0 else len(self.body)
        out, self.body = self.body[:n], self.body[n:]
        return out


def test_slow_reply_stops_at_the_deadline():
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        read_with_deadline(SlowResponse(), max_bytes=1000, deadline_seconds=0.1)
    assert time.monotonic() - started < 0.5


def test_timeout_is_an_oserror_so_callers_treat_it_as_a_failed_fetch():
    assert issubclass(TimeoutError, OSError)


def test_whole_reply_is_read_when_it_arrives_in_time():
    assert read_with_deadline(PlainResponse(b"hello world"), max_bytes=100, deadline_seconds=5) == b"hello world"


def test_reply_is_cut_at_max_bytes():
    assert read_with_deadline(PlainResponse(b"hello world"), max_bytes=5, deadline_seconds=5) == b"hello"


def test_read_page_gives_up_on_a_slow_page(monkeypatch):
    import reservations

    monkeypatch.setattr(reservations, "PAGE_DEADLINE_SECONDS", 0.1)
    monkeypatch.setattr(reservations.urllib.request, "urlopen", lambda request, timeout: SlowResponse())
    started = time.monotonic()
    with pytest.raises(OSError):
        reservations.read_page("https://slow.example/")
    assert time.monotonic() - started < 0.5


def test_onemap_gives_up_on_a_slow_reply(monkeypatch):
    import travel

    monkeypatch.setattr(travel, "DEADLINE_SECONDS", 0.1)
    monkeypatch.setattr(travel, "urlopen", lambda request, timeout: SlowResponse(content_type="application/json"))
    with pytest.raises(travel.TravelError, match="taking too long"):
        travel.OneMap("token").find("Tiong Bahru")


def test_page_loads_never_reads_the_body(monkeypatch):
    import links

    response = SlowResponse()
    response.status = 200
    monkeypatch.setattr(links.urllib.request, "urlopen", lambda request, timeout: response)
    assert links.page_loads("https://slow.example/")
    assert response.reads == 0
