"""Tests for google_transit.py and for main.py trying Google first.

No network. httpx.post is replaced with a fake that returns a sample Routes API answer (or raises).
The key is a made-up string, never a real one.
"""

import pathlib
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import google_transit  # noqa: E402
import main  # noqa: E402
import transit  # noqa: E402

HOUSTON = ZoneInfo("America/Chicago")
FAKE_KEY = "FAKE-KEY-FOR-TESTS"
SESSION = {"lat": 29.7200, "lng": -95.3500, "language": "en"}
PLACE = {"lat": 29.7300, "lng": -95.3500}


def at(year, month, day, hour, minute):
    """A Houston-time datetime for the tests (October is daylight time, UTC-5)."""
    return datetime(year, month, day, hour, minute, tzinfo=HOUSTON)


def walk(seconds):
    """A WALK step as Google sends it."""
    return {"travelMode": "WALK", "staticDuration": f"{seconds}s"}


def bus(route, headsign, board, alight, departs_utc, arrives_utc, stops):
    """A TRANSIT step. Times are UTC, as Google sends them (21:12Z is 4:12 PM in Houston)."""
    return {
        "travelMode": "TRANSIT",
        "staticDuration": "1440s",
        "transitDetails": {
            "stopDetails": {
                "departureStop": {"name": board},
                "arrivalStop": {"name": alight},
                "departureTime": departs_utc,
                "arrivalTime": arrives_utc,
            },
            "headsign": headsign,
            "stopCount": stops,
            "transitLine": {"nameShort": route, "name": "Scott", "vehicle": {"type": "BUS"}},
        },
    }


def response(*steps):
    """A Routes API answer with one route and one leg holding these steps."""
    return {"routes": [{"legs": [{"duration": "1800s", "steps": list(steps)}]}]}


# Walk 5.2 minutes to the stop, bus 54 at 4:12 PM (21:12Z) to 4:36 PM, then walk 4 minutes.
ONE_BUS = response(
    walk(312),
    bus("54", "Downtown", "Scott @ Holman", "Main @ Gray", "2026-10-12T21:12:00Z", "2026-10-12T21:36:00Z", 6),
    walk(240),
)

# Two transfers: 54, then 40, then 25, with short walks between them.
TWO_TRANSFERS = response(
    walk(120),
    bus("54", "Downtown", "Scott @ Holman", "Elgin @ Main", "2026-10-12T21:12:00Z", "2026-10-12T21:24:00Z", 2),
    walk(60),
    bus("40", "Uptown", "Elgin @ Main", "Walker @ Main", "2026-10-12T21:30:00Z", "2026-10-12T21:45:00Z", 4),
    walk(30),
    bus("25", "Downtown", "Walker @ Main", "Main @ Gray", "2026-10-12T21:50:00Z", "2026-10-12T22:05:00Z", 3),
    walk(180),
)

WALK_ONLY = response(walk(900))


class FakeResponse:
    """Only what google_transit reads: a status code and the JSON body."""

    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data

    def json(self):
        return self._data


@pytest.fixture
def google(monkeypatch):
    """Turns the key on and replaces httpx.post. Put the answer (or an exception) in state["reply"]."""
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", FAKE_KEY)
    state = {"calls": [], "reply": FakeResponse(200, ONE_BUS)}

    def fake_post(url, **kwargs):
        state["calls"].append({"url": url, **kwargs})
        if isinstance(state["reply"], Exception):
            raise state["reply"]
        return state["reply"]

    monkeypatch.setattr(google_transit.httpx, "post", fake_post)
    return state


def test_enabled_only_when_a_key_is_set(monkeypatch):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    assert google_transit.enabled() is False
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "   ")
    assert google_transit.enabled() is False
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", FAKE_KEY)
    assert google_transit.enabled() is True


def test_one_bus_route_becomes_the_transit_dict(google):
    found = google_transit.directions(29.72, -95.35, 29.73, -95.35, when=at(2026, 10, 12, 16, 0))
    assert found == {
        "legs": [{
            "route": "54", "headsign": "Downtown", "board_stop": "Scott @ Holman", "alight_stop": "Main @ Gray",
            "departs": "16:12", "arrives": "16:36", "stops": 6,
        }],
        "walk_to_min": 6,     # 312 seconds, rounded up
        "walk_from_min": 4,   # 240 seconds
        "arrive_by": "16:40",  # the last arrival plus the walk home
    }


def test_two_transfers_give_three_legs(google):
    google["reply"] = FakeResponse(200, TWO_TRANSFERS)
    found = google_transit.directions(29.72, -95.35, 29.73, -95.35, when=at(2026, 10, 12, 16, 0))
    assert [leg["route"] for leg in found["legs"]] == ["54", "40", "25"]
    assert [leg["departs"] for leg in found["legs"]] == ["16:12", "16:30", "16:50"]
    assert found["legs"][1]["board_stop"] == "Elgin @ Main" and found["legs"][1]["stops"] == 4
    assert found["walk_to_min"] == 2 and found["walk_from_min"] == 3
    assert found["arrive_by"] == "17:08"  # 17:05 arrival, plus 3 minutes
    assert "later" not in found


def test_walking_only_route_is_none(google):
    google["reply"] = FakeResponse(200, WALK_ONLY)
    assert google_transit.directions(29.72, -95.35, 29.73, -95.35, when=at(2026, 10, 12, 16, 0)) is None


def test_http_403_is_none_and_prints_one_line(google, capsys):
    google["reply"] = FakeResponse(403, {"error": {"message": "API not enabled"}})
    assert google_transit.directions(29.72, -95.35, 29.73, -95.35, when=at(2026, 10, 12, 16, 0)) is None
    out = capsys.readouterr().out
    assert "HTTP 403" in out and out.count("\n") == 1


def test_timeout_is_none_and_does_not_raise(google, capsys):
    google["reply"] = httpx.ReadTimeout("timed out")
    assert google_transit.directions(29.72, -95.35, 29.73, -95.35, when=at(2026, 10, 12, 16, 0)) is None
    assert "ReadTimeout" in capsys.readouterr().out


def test_unexpected_body_is_none_and_does_not_raise(google):
    google["reply"] = FakeResponse(200, {"unexpected": True})
    assert google_transit.directions(29.72, -95.35, 29.73, -95.35, when=at(2026, 10, 12, 16, 0)) is None


def test_request_has_key_field_mask_transit_mode_and_departure_time(google):
    google_transit.directions(29.72, -95.35, 29.73, -95.35, when=at(2026, 10, 12, 16, 0))
    call = google["calls"][0]
    assert call["url"] == "https://routes.googleapis.com/directions/v2:computeRoutes"
    assert call["headers"]["X-Goog-Api-Key"] == FAKE_KEY
    mask = call["headers"]["X-Goog-FieldMask"].split(",")
    assert mask == [
        "routes.legs.steps.travelMode", "routes.legs.steps.staticDuration",
        "routes.legs.steps.transitDetails", "routes.legs.duration",
    ]
    body = call["json"]
    assert body["travelMode"] == "TRANSIT"
    assert body["departureTime"] == "2026-10-12T21:00:00Z"  # 4:00 PM Houston time, in UTC
    assert body["origin"]["location"]["latLng"] == {"latitude": 29.72, "longitude": -95.35}
    assert body["destination"]["location"]["latLng"] == {"latitude": 29.73, "longitude": -95.35}
    assert body["units"] == "IMPERIAL"
    assert call["timeout"] == 6


def test_language_is_sent_as_google_code(google):
    google_transit.directions(29.72, -95.35, 29.73, -95.35, when=at(2026, 10, 12, 16, 0), language="zh")
    assert google["calls"][0]["json"]["languageCode"] == "zh-CN"
    google_transit.directions(29.72, -95.35, 29.73, -95.35, when=at(2026, 10, 12, 16, 0), language="ar")
    assert google["calls"][1]["json"]["languageCode"] == "ar"


def test_first_bus_far_away_is_later(google):
    # 8:00 AM, and the first bus leaves at 4:12 PM: more than 90 minutes away.
    found = google_transit.directions(29.72, -95.35, 29.73, -95.35, when=at(2026, 10, 12, 8, 0))
    assert found["later"] is True
    assert found["buses_running"] is None
    assert found["legs"][0]["departs"] == "16:12"


def test_first_bus_soon_is_not_later(google):
    found = google_transit.directions(29.72, -95.35, 29.73, -95.35, when=at(2026, 10, 12, 16, 0))
    assert "later" not in found and "buses_running" not in found


def test_no_key_means_no_request(google, monkeypatch):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY")
    assert google_transit.enabled() is False
    assert google_transit.directions(29.72, -95.35, 29.73, -95.35, when=at(2026, 10, 12, 16, 0)) is None
    assert google["calls"] == []


def test_main_tries_google_first(google, monkeypatch):
    metro_calls = []
    monkeypatch.setattr(transit, "directions", lambda *args, **kwargs: metro_calls.append(args) or "METRO")
    found = main.bus_for(SESSION, PLACE)
    assert found["legs"][0]["route"] == "54"
    assert metro_calls == []
    assert len(google["calls"]) == 1


def test_main_passes_the_session_language(google, monkeypatch):
    monkeypatch.setattr(transit, "directions", lambda *args, **kwargs: None)
    main.bus_for({**SESSION, "language": "es"}, PLACE)
    assert google["calls"][0]["json"]["languageCode"] == "es"


def test_main_falls_back_to_metro_when_google_has_no_route(google, monkeypatch):
    google["reply"] = FakeResponse(200, WALK_ONLY)
    monkeypatch.setattr(transit, "directions", lambda *args, **kwargs: "METRO")
    assert main.bus_for(SESSION, PLACE) == "METRO"


def test_main_falls_back_to_metro_when_google_fails(google, monkeypatch):
    google["reply"] = FakeResponse(403, {})
    monkeypatch.setattr(transit, "directions", lambda *args, **kwargs: "METRO")
    assert main.bus_for(SESSION, PLACE) == "METRO"


def test_main_without_key_uses_metro_only(monkeypatch):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(args)
        raise AssertionError("no key: Google must not be called")

    monkeypatch.setattr(google_transit.httpx, "post", fake_post)
    monkeypatch.setattr(transit, "directions", lambda *args, **kwargs: "METRO")
    assert main.bus_for(SESSION, PLACE) == "METRO"
    assert calls == []


def test_bus_data_available_with_google_alone(monkeypatch, tmp_path):
    monkeypatch.setattr(main.db, "enabled", lambda: False)
    monkeypatch.setattr(transit, "LOCAL_PATH", tmp_path / "missing.json")
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    assert main.bus_data_available() is False
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", FAKE_KEY)
    assert main.bus_data_available() is True


def test_later_reply_says_the_next_bus_for_google(google):
    found = google_transit.directions(29.72, -95.35, 29.73, -95.35, when=at(2026, 10, 12, 8, 0))
    text = transit.summary(found, "en", "sms")
    assert text.startswith("The next bus that goes there: Bus 54 from Scott@Holman 4:12PM")
    assert transit.summary(found, "en", "kiosk").startswith("The next bus that goes there: Walk 6 minutes")
