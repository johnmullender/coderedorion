"""Find open help resources near a spot, using the hours in data/resources.json."""

import json
import math
import pathlib
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

DATA_DIR = pathlib.Path(__file__).resolve().parent / "data"
HOUSTON = ZoneInfo("America/Chicago")
DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]  # index matches datetime.weekday()
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MAX_LOOKAHEAD = timedelta(days=7)
EARTH_RADIUS_MI = 3958.8

_cache = None  # the default resources.json, read once


def load_resources(path=None):
    """Read the resource list. The default file is cached after the first read."""
    global _cache
    if path is not None:
        return _read_json(path)
    if _cache is None:
        _cache = _read_json(DATA_DIR / "resources.json")
    return _cache


def _read_json(path):
    """Load a JSON file and return its contents."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def haversine_miles(lat1, lng1, lat2, lng2):
    """Straight-line distance between two lat/lng points, in miles."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lam = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lam / 2) ** 2
    return 2 * EARTH_RADIUS_MI * math.asin(math.sqrt(a))


def _to_minutes(hhmm):
    """Turn 'HH:MM' into minutes since midnight. '24:00' gives 1440."""
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _hhmm(minutes):
    """Turn minutes since midnight back into 'HH:MM'."""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _split_range(text):
    """Turn '18:00-07:00' into (1080, 420)."""
    start, end = text.split("-")
    return _to_minutes(start), _to_minutes(end)


def _ranges_for(resource, weekday):
    """All (start, end) minute pairs for one weekday (0 = Monday)."""
    key = DAY_KEYS[weekday]
    return [_split_range(text) for text in (resource.get("hours") or {}).get(key) or []]


def _current_range(resource, when):
    """Return the (start, end) minutes of the range open at `when`, or None if closed."""
    now = when.hour * 60 + when.minute
    today = when.weekday()
    for start, end in _ranges_for(resource, today):
        if end > start and start <= now < end:  # normal range, we are inside it
            return (start, end)
        if end < start and now >= start:  # crosses midnight, we are in the evening part
            return (start, end)
    # A midnight-crossing range from yesterday keeps going until its end time this morning.
    yesterday = (today - 1) % 7
    for start, end in _ranges_for(resource, yesterday):
        if end < start and now < end:
            return (start, end)
    return None


def _closes_at(rng):
    """The end of an open range as 'HH:MM', or None for an all-day range."""
    if rng is None:
        return None
    start, end = rng
    if start == 0 and end == 1440:
        return None
    return _hhmm(end)  # a range ending at midnight shows as '24:00'


def is_open(resource, when):
    """True if the resource is open at the tz-aware datetime `when`."""
    return _current_range(resource, when) is not None


def _has_location(resource):
    """True if the resource has both lat and lng."""
    return resource.get("lat") is not None and resource.get("lng") is not None


def _in_need(resource, need):
    """True if the resource lists this need in its categories."""
    return need in (resource.get("category") or [])


def _make_match(resource, lat, lng):
    """Copy a resource and add the distance and status fields with safe defaults."""
    match = dict(resource)
    match["distance_mi"] = round(haversine_miles(lat, lng, resource["lat"], resource["lng"]), 1)
    match["open_now"] = False
    match["closes_at"] = None
    match["opens_at"] = None
    return match


def _next_start(resource, when):
    """The earliest opening time after `when`, within 7 days, or None."""
    best = None
    for day_offset in range(8):  # today plus the next 7 days
        day = when + timedelta(days=day_offset)
        for start, _end in _ranges_for(resource, day.weekday()):
            opening = datetime.combine(day.date(), time(start // 60, start % 60), tzinfo=when.tzinfo)
            if when < opening <= when + MAX_LOOKAHEAD and is_open(resource, opening):
                if best is None or opening < best:
                    best = opening
    return best


def _soonest_opening(candidates, lat, lng, when):
    """Find the closed resource that opens soonest. Ties go to the nearest one."""
    best_start = None
    best_resource = None
    # Sorting by distance first means a tie on time keeps the nearest place.
    by_distance = sorted(candidates, key=lambda r: haversine_miles(lat, lng, r["lat"], r["lng"]))
    for resource in by_distance:
        start = _next_start(resource, when)
        if start is not None and (best_start is None or start < best_start):
            best_start = start
            best_resource = resource
    if best_resource is None:
        return None
    match = _make_match(best_resource, lat, lng)
    match["opens_at"] = f"{DAY_NAMES[best_start.weekday()]} {_hhmm(best_start.hour * 60 + best_start.minute)}"
    return match


def find_matches(need, lat, lng, when=None, resources=None, limit=3):
    """Return up to `limit` open places for `need`, nearest first, plus next_open if none are open."""
    if when is None:
        when = datetime.now(HOUSTON)
    if resources is None:
        resources = load_resources()
    candidates = [r for r in resources if _in_need(r, need) and _has_location(r)]
    open_matches = []
    for resource in candidates:
        rng = _current_range(resource, when)
        if rng is not None:
            match = _make_match(resource, lat, lng)
            match["open_now"] = True
            match["closes_at"] = _closes_at(rng)
            open_matches.append(match)
    open_matches.sort(key=lambda m: m["distance_mi"])
    options = open_matches[:limit]
    next_open = None
    if not options:
        next_open = _soonest_opening(candidates, lat, lng, when)
    return {"options": options, "next_open": next_open}
