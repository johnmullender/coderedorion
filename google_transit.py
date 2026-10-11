"""Bus directions from the Google Maps Routes API: the first choice for the help line.

directions() asks Google for a TRANSIT route and turns it into the same dict that
transit.directions() returns, so transit.summary() reads both the same way. When this
returns None (no key, no bus route, an error or a timeout), main.py falls back to the
METRO schedule in transit.py.

Nothing here raises. Every problem prints one line and returns None.
"""

import math
import os
import pathlib
from datetime import datetime, timedelta

import httpx
from dotenv import load_dotenv

import transit

ROOT = pathlib.Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
# Only these fields come back, so Google sends (and bills) no more than we read.
FIELD_MASK = ("routes.legs.steps.travelMode,routes.legs.steps.staticDuration,"
              "routes.legs.steps.transitDetails,routes.legs.duration")
TIMEOUT_SECONDS = 6

# Our language codes as Google writes them. Everything else is already the same code.
LANGUAGE_CODES = {"zh": "zh-CN"}


def _key():
    """The API key from the environment (or .env), without spaces. Empty when not set."""
    return os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()


def enabled():
    """True when a Google Maps key is set. Checked on every call."""
    return bool(_key())


def _seconds(text):
    """A Google duration such as '312s' as a number of seconds."""
    return float(str(text).rstrip("s"))


def _parse(text):
    """A Google time such as '2026-10-12T21:12:00Z' as an aware datetime (Python 3.10 does not read the 'Z')."""
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _walk_minutes(seconds):
    """Walking minutes for a total of seconds, rounded up, never less than 1 (only called when there is walking)."""
    return max(1, math.ceil(seconds / 60))


def _result(data, now):
    """The transit.directions() dict for the first route in Google's answer, or None when it has no bus."""
    routes = data.get("routes") or []
    legs = (routes[0].get("legs") or [{}]) if routes else [{}]
    steps = legs[0].get("steps") or []

    rides = []    # the TRANSIT steps, in order
    times = []    # (departs, arrives) datetimes for each ride
    before = []   # seconds of WALK before the first ride
    after = []    # seconds of WALK after the last ride
    for step in steps:
        if step.get("travelMode") == "TRANSIT" and step.get("transitDetails"):
            ride = step["transitDetails"]
            rides.append(ride)
            stops = ride["stopDetails"]
            times.append((_parse(stops["departureTime"]), _parse(stops["arrivalTime"])))
            after = []  # walking after this ride so far is none
        elif step.get("travelMode") == "WALK":
            (after if rides else before).append(_seconds(step.get("staticDuration", "0s")))

    if not rides:
        return None  # walking only: no bus route

    result_legs = []
    for ride, (departs, arrives) in zip(rides, times):
        line = ride.get("transitLine") or {}
        stops = ride["stopDetails"]
        result_legs.append({
            "route": line.get("nameShort") or line.get("name") or "",
            "headsign": ride.get("headsign") or "",
            "board_stop": (stops.get("departureStop") or {}).get("name") or "",
            "alight_stop": (stops.get("arrivalStop") or {}).get("name") or "",
            "departs": transit._hhmm(departs),
            "arrives": transit._hhmm(arrives),
            "stops": int(ride.get("stopCount") or 0),
        })

    walk_to_min = _walk_minutes(sum(before)) if before else 1  # the GTFS search always walks at least a minute too
    walk_from_min = _walk_minutes(sum(after)) if after else 1
    last_arrival = times[-1][1]
    result = {
        "legs": result_legs,
        "walk_to_min": walk_to_min,
        "walk_from_min": walk_from_min,
        "arrive_by": transit._hhmm(last_arrival + timedelta(minutes=walk_from_min)),
    }
    # Nothing leaves within the normal wait: say the first ride is later on, as transit.py does.
    wait_min = (times[0][0] - now).total_seconds() / 60
    if wait_min > transit.MAX_WAIT_MIN:
        result["later"] = True
        result["buses_running"] = None  # Google does not say whether other buses run
    return result


def directions(from_lat, from_lng, to_lat, to_lng, when=None, language="en"):
    """The bus route between two points from Google, in transit.directions() form, or None.

    `when` is the departure time (None means now; a naive time is Houston time). `language` is our
    code for the reply language (en, es, vi, zh, ar).
    """
    key = _key()
    if not key:
        return None
    try:
        now = transit._as_utc(when)
        body = {
            "origin": {"location": {"latLng": {"latitude": from_lat, "longitude": from_lng}}},
            "destination": {"location": {"latLng": {"latitude": to_lat, "longitude": to_lng}}},
            "travelMode": "TRANSIT",
            "departureTime": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "languageCode": LANGUAGE_CODES.get(language, language),
            "units": "IMPERIAL",
        }
        headers = {"X-Goog-Api-Key": key, "X-Goog-FieldMask": FIELD_MASK}
        response = httpx.post(ROUTES_URL, headers=headers, json=body, timeout=TIMEOUT_SECONDS)
        if response.status_code != 200:
            print(f"Google directions: HTTP {response.status_code}, using METRO schedule")
            return None
        return _result(response.json(), now)
    except Exception as problem:
        print(f"Google directions failed ({type(problem).__name__}: {problem}), using METRO schedule")
        return None
