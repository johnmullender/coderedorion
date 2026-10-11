"""METRO bus directions from the GTFS schedule.

The schedule lives in Tiger Data (tables gtfs_*, loaded by scripts/load_gtfs.py).
Without DATABASE_URL, data/transit_local.json is used instead (written by
`scripts/load_gtfs.py --local LAT LNG`). Nothing here raises to the caller: a
database problem gives empty results, and directions() returns None when no ride
is found. A DbSource opens one connection lazily and directions() closes it at the end.
"""

import json
import math
import os
import pathlib
import re
import time
from datetime import datetime, time as clock_time, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg
from dotenv import load_dotenv

from matching import haversine_miles

ROOT = pathlib.Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

LOCAL_PATH = ROOT / "data" / "transit_local.json"
HOUSTON = ZoneInfo("America/Chicago")
UTC = timezone.utc
WALK_MPH = 3.0
MAX_WALK_MI = 0.5
MAX_WAIT_MIN = 90
LATER_WAIT_MIN = 24 * 60  # when nothing leaves within MAX_WAIT_MIN, look this far ahead for a ride
LATER_BATCHES = 20  # the 24-hour search reads at most this many batches of MAX_DEPARTURES trips
LATER_TRANSFER_BATCHES = 3  # the 24-hour search tries transfers from only the first few batches
TIME_BUDGET_SEC = 8.0  # directions() gives up after this long, so the kiosk never times out
NEAREST_STOP_MILES = 2.0  # how far to look for the stop nearest a point, when no ride is found
TRANSFER_MIN_MIN = 2  # minutes needed between getting off one bus and boarding the next
MAX_DEPARTURES = 40  # per search: the earliest this many trips (one entry per trip) are considered
DOWN_SECONDS = 30  # after a connection failure, skip the database this long
WEEKDAY_COLUMNS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

# When the database cannot be reached, we stop trying until this time (monotonic seconds).
_down_until = 0.0
# True while we are in an outage whose warning has already been printed.
_outage_reported = False


class GtfsSource:
    """The schedule queries that directions() uses. DbSource and LocalSource implement these."""

    def stops_near(self, lat, lng, miles):
        """Stops within `miles` of a point: [{"stop_id", "name", "lat", "lng"}]."""
        raise NotImplementedError

    def service_ids(self, day):
        """Service ids running on a date, with calendar_dates changes applied: a set of strings."""
        raise NotImplementedError

    def departures(self, stop_ids, after_sec, before_sec, service_ids):
        """Departures from these stops between two times (seconds after midnight of the service day).

        Returns [{"trip_id", "route", "headsign", "stop_id", "stop_sequence", "dep_sec"}].
        Stops where riders cannot board are left out.
        """
        raise NotImplementedError

    def trip_stops_after(self, trip_id, stop_sequence):
        """The stops of a trip after `stop_sequence`, in order, including stops where riders cannot get off.

        Returns [{"stop_id", "name", "stop_sequence", "arr_sec", "lat", "lng", "drop_off"}]
        where drop_off is False for stops that do not let riders off.
        """
        raise NotImplementedError

    def trip_stops_after_many(self, trip_ids):
        """Every stop of several trips in one call: {trip_id: [stop dicts as above, from the first stop]}."""
        return {trip_id: self.trip_stops_after(trip_id, 0) for trip_id in trip_ids}

    def close(self):
        """Release any open connection. Nothing to do by default."""


def _apply_exceptions(base, exceptions):
    """Add the service ids marked exception_type 1 and remove those marked 2."""
    services = set(base)
    for service_id, kind in exceptions:
        if kind == 1:
            services.add(service_id)
        elif kind == 2:
            services.discard(service_id)
    return services


class DbSource(GtfsSource):
    """Schedule queries against the gtfs_* tables in Tiger Data. Never raises."""

    def __init__(self, url=None):
        self._dsn = url
        self._conn = None

    def _url(self):
        """The connection URL: the one given to the constructor, else DATABASE_URL (read fresh)."""
        return (self._dsn or os.environ.get("DATABASE_URL", "")).strip()

    def _connection(self):
        """The shared connection for this source, opened on first use."""
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(self._url(), connect_timeout=5, autocommit=True)
        return self._conn

    def close(self):
        """Close the connection. The next query opens a new one."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = None

    def _rows(self, sql, params=()):
        """Run one query. Any failure closes the connection and gives an empty list."""
        global _down_until, _outage_reported
        if not self._url() or time.monotonic() < _down_until:
            return []
        try:
            rows = self._connection().execute(sql, params).fetchall()
            _outage_reported = False
            return rows
        except Exception as problem:
            self.close()
            if isinstance(problem, psycopg.OperationalError):
                _down_until = time.monotonic() + DOWN_SECONDS
            if not _outage_reported:
                _outage_reported = True
                print(f"Transit database unavailable, no bus data: {problem}")
            return []

    def stops_near(self, lat, lng, miles):
        """Stops in a box around the point, then the exact distance check in Python."""
        degrees_lat = miles / 69.0
        degrees_lng = miles / (69.0 * max(0.2, math.cos(math.radians(lat))))
        rows = self._rows(
            "SELECT stop_id, stop_name, stop_lat, stop_lon FROM gtfs_stops "
            "WHERE stop_lat BETWEEN %s AND %s AND stop_lon BETWEEN %s AND %s "
            "AND (location_type IS NULL OR location_type = 0)",
            (lat - degrees_lat, lat + degrees_lat, lng - degrees_lng, lng + degrees_lng),
        )
        stops = []
        for stop_id, name, stop_lat, stop_lon in rows:
            if haversine_miles(lat, lng, stop_lat, stop_lon) <= miles:
                stops.append({"stop_id": stop_id, "name": name, "lat": stop_lat, "lng": stop_lon})
        return stops

    def service_ids(self, day):
        """Services whose calendar row covers the day, then the calendar_dates changes for it."""
        column = WEEKDAY_COLUMNS[day.weekday()]
        base = [
            row[0]
            for row in self._rows(
                f"SELECT service_id FROM gtfs_calendar "
                f"WHERE {column} AND start_date <= %s AND end_date >= %s",
                (day, day),
            )
        ]
        exceptions = self._rows(
            'SELECT service_id, exception_type FROM gtfs_calendar_dates WHERE "date" = %s',
            (day,),
        )
        return _apply_exceptions(base, exceptions)

    def departures(self, stop_ids, after_sec, before_sec, service_ids):
        """Departures from the stops in a time window, for the given services."""
        if not stop_ids or not service_ids:
            return []
        rows = self._rows(
            "SELECT st.trip_id, r.route_short_name, r.route_id, t.trip_headsign, "
            "st.stop_id, st.stop_sequence, st.departure "
            "FROM gtfs_stop_times st "
            "JOIN gtfs_trips t ON t.trip_id = st.trip_id "
            "JOIN gtfs_routes r ON r.route_id = t.route_id "
            "WHERE st.stop_id = ANY(%s) AND st.departure BETWEEN %s AND %s "
            "AND t.service_id = ANY(%s) AND COALESCE(st.pickup_type, 0) <> 1 "
            "ORDER BY st.departure",
            (list(stop_ids), after_sec, before_sec, list(service_ids)),
        )
        return [
            {
                "trip_id": row[0],
                "route": row[1] or row[2],
                "headsign": row[3] or "",
                "stop_id": row[4],
                "stop_sequence": row[5],
                "dep_sec": row[6],
            }
            for row in rows
        ]

    def trip_stops_after(self, trip_id, stop_sequence):
        """The stops of one trip after the given position (see GtfsSource)."""
        return self._trip_stops(trip_id, stop_sequence)[1]

    def trip_stops_after_many(self, trip_ids):
        """All stops of several trips in one query."""
        trip_ids = list(trip_ids)
        found = {trip_id: [] for trip_id in trip_ids}
        if not trip_ids:
            return found
        rows = self._rows(
            "SELECT st.trip_id, st.stop_id, s.stop_name, st.stop_sequence, st.arrival, "
            "s.stop_lat, s.stop_lon, COALESCE(st.drop_off_type, 0) <> 1 "
            "FROM gtfs_stop_times st JOIN gtfs_stops s ON s.stop_id = st.stop_id "
            "WHERE st.trip_id = ANY(%s) ORDER BY st.trip_id, st.stop_sequence",
            (trip_ids,),
        )
        for row in rows:
            found[row[0]].append(_stop_dict(row[1], row[2], row[3], row[4], row[5], row[6], row[7]))
        return found

    def _trip_stops(self, trip_id, stop_sequence):
        """(trip_id, stops after stop_sequence) for one trip."""
        rows = self._rows(
            "SELECT st.stop_id, s.stop_name, st.stop_sequence, st.arrival, s.stop_lat, s.stop_lon, "
            "COALESCE(st.drop_off_type, 0) <> 1 "
            "FROM gtfs_stop_times st JOIN gtfs_stops s ON s.stop_id = st.stop_id "
            "WHERE st.trip_id = %s AND st.stop_sequence > %s ORDER BY st.stop_sequence",
            (trip_id, stop_sequence),
        )
        return trip_id, [_stop_dict(*row) for row in rows]


def _stop_dict(stop_id, name, sequence, arrival, lat, lng, drop_off):
    """One trip stop in the shape the GtfsSource interface returns."""
    return {
        "stop_id": stop_id,
        "name": name,
        "stop_sequence": sequence,
        "arr_sec": arrival,
        "lat": lat,
        "lng": lng,
        "drop_off": bool(drop_off),
    }


class LocalSource(GtfsSource):
    """Schedule read from a JSON file written by `scripts/load_gtfs.py --local`. Never raises."""

    def __init__(self, path=None):
        self._path = pathlib.Path(path) if path else LOCAL_PATH
        self._loaded = False
        self._stops = []
        self._trips = {}
        self._by_stop = {}
        self._calendar = []
        self._calendar_dates = []

    def _load(self):
        """Read the file once. A missing or broken file leaves everything empty."""
        if self._loaded:
            return
        self._loaded = True
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            self._stops = data.get("stops", [])
            self._calendar = data.get("calendar", [])
            self._calendar_dates = data.get("calendar_dates", [])
            for trip in data.get("trips", []):
                self._trips[trip["trip_id"]] = trip
                for position, stop in enumerate(trip["stops"]):
                    self._by_stop.setdefault(stop["stop_id"], []).append((trip, position))
        except Exception as problem:
            print(f"Local transit file unreadable: {problem}")

    def stops_near(self, lat, lng, miles):
        """Stops in the file (all stops served by the saved trips) within the distance."""
        self._load()
        return [
            {"stop_id": s["stop_id"], "name": s["name"], "lat": s["lat"], "lng": s["lng"]}
            for s in self._stops
            if haversine_miles(lat, lng, s["lat"], s["lng"]) <= miles
        ]

    def service_ids(self, day):
        """Calendar rows that run on this weekday and date range, plus calendar_dates changes."""
        self._load()
        iso = day.isoformat()
        base = {
            c["service_id"]
            for c in self._calendar
            if c["start_date"] <= iso <= c["end_date"] and c["days"][day.weekday()]
        }
        exceptions = [
            (e["service_id"], e["exception_type"]) for e in self._calendar_dates if e["date"] == iso
        ]
        return _apply_exceptions(base, exceptions)

    def departures(self, stop_ids, after_sec, before_sec, service_ids):
        """Departures from the stops in a time window, for the given services."""
        self._load()
        rows = []
        for stop_id in stop_ids:
            for trip, position in self._by_stop.get(stop_id, []):
                stop = trip["stops"][position]
                if trip["service_id"] not in service_ids or stop.get("pickup_type") == 1:
                    continue
                if after_sec <= stop["departure"] <= before_sec:
                    rows.append(
                        {
                            "trip_id": trip["trip_id"],
                            "route": trip["route"],
                            "headsign": trip["headsign"],
                            "stop_id": stop_id,
                            "stop_sequence": stop["stop_sequence"],
                            "dep_sec": stop["departure"],
                        }
                    )
        rows.sort(key=lambda r: r["dep_sec"])
        return rows

    def trip_stops_after(self, trip_id, stop_sequence):
        """The stops of one trip after the given position, with the drop_off flag (see GtfsSource)."""
        self._load()
        trip = self._trips.get(trip_id)
        if trip is None:
            return []
        return [
            {
                "stop_id": s["stop_id"],
                "name": s["name"],
                "stop_sequence": s["stop_sequence"],
                "arr_sec": s["arrival"],
                "lat": s["lat"],
                "lng": s["lng"],
                "drop_off": s.get("drop_off_type") != 1,
            }
            for s in trip["stops"]
            if s["stop_sequence"] > stop_sequence
        ]


def _default_source():
    """DbSource when DATABASE_URL is set, else LocalSource when the local file exists, else None."""
    if os.environ.get("DATABASE_URL", "").strip():
        return DbSource()
    if LOCAL_PATH.exists():
        return LocalSource(LOCAL_PATH)
    return None


def _walk_minutes(miles):
    """Walking minutes for a distance, rounded up, never less than 1."""
    return max(1, math.ceil(miles / WALK_MPH * 60))


def _as_utc(when):
    """The search start as a UTC datetime. None means now; a naive time is Houston time."""
    if when is None:
        return datetime.now(UTC)
    if when.tzinfo is None:
        when = when.replace(tzinfo=HOUSTON)
    return when.astimezone(UTC)


def _service_origin(day):
    """Where a GTFS service day starts: noon minus 12 hours, as an absolute (UTC) time.

    GTFS defines times from this origin, so on daylight-saving days it is not always midnight.
    """
    noon = datetime.combine(day, clock_time(12, 0), tzinfo=HOUSTON)
    return noon.astimezone(UTC) - timedelta(hours=12)


def _hhmm(moment):
    """An absolute time as 24-hour 'HH:MM' in Houston (times after midnight show as 00:xx, 01:xx ...)."""
    return moment.astimezone(HOUSTON).strftime("%H:%M")


def _near(source, lat, lng):
    """Stops within the walking limit of a point, keyed by stop_id, with walking minutes."""
    found = {}
    for stop in source.stops_near(lat, lng, MAX_WALK_MI) or []:
        miles = haversine_miles(lat, lng, stop["lat"], stop["lng"])
        if miles <= MAX_WALK_MI:
            found[stop["stop_id"]] = {
                "stop_id": stop["stop_id"],
                "name": stop["name"],
                "walk_min": _walk_minutes(miles),
            }
    return found


def _service_days(start, end):
    """Service days that can have trips in the window: the day before start's Houston date, through end's."""
    day = start.astimezone(HOUSTON).date() - timedelta(days=1)
    last = end.astimezone(HOUSTON).date()
    days = []
    while day <= last:
        days.append(day)
        day += timedelta(days=1)
    return days


class _SearchTimeout(Exception):
    """The search ran past TIME_BUDGET_SEC."""


class _Search:
    """Caches trip lookups for one directions() call and turns service days into real times."""

    def __init__(self, source, budget_sec=None):
        self.source = source
        self._trips = {}  # trip_id -> every stop of the trip, in order
        self._services = {}  # day -> service ids, so each day is looked up once
        self._deadline = time.monotonic() + (TIME_BUDGET_SEC if budget_sec is None else budget_sec)

    def check_time(self):
        """Stop the search once the time budget is used up."""
        if time.monotonic() > self._deadline:
            raise _SearchTimeout()

    def service_ids(self, day):
        """The service ids for a day, looked up once per search."""
        if day not in self._services:
            self._services[day] = self.source.service_ids(day)
        return self._services[day]

    def load_trips(self, trip_ids):
        """Fetch the stop lists of trips not loaded yet, in one call."""
        missing = {trip_id for trip_id in trip_ids if trip_id not in self._trips}
        if missing:
            self.check_time()
            found = self.source.trip_stops_after_many(list(missing)) or {}
            for trip_id in missing:
                self._trips[trip_id] = found.get(trip_id, [])

    def riding(self, dep):
        """The stops after a boarding, as (rides, stop) pairs.

        `rides` counts every stop passed, including stops where riders cannot get off.
        """
        stops = self._trips.get(dep["trip_id"], [])
        board = next((i for i, s in enumerate(stops) if s["stop_sequence"] == dep["stop_sequence"]), None)
        if board is None:
            return []
        return [(index - board, stops[index]) for index in range(board + 1, len(stops))]

    def departures(self, stop_ids, start, end):
        """Departures between two absolute times, one per trip, at most MAX_DEPARTURES, earliest first.

        Each service day is searched with its own offsets, because GTFS times past 24:00 belong
        to the service day that started the day before.
        """
        if not stop_ids:
            return []
        rows = []
        for day in _service_days(start, end):
            self.check_time()
            services = self.service_ids(day)
            if not services:
                continue
            origin = _service_origin(day)
            after = max(0, int((start - origin).total_seconds()))
            before = int((end - origin).total_seconds())
            if before < after:
                continue
            for dep in self.source.departures(list(stop_ids), after, before, services) or []:
                dep_dt = origin + timedelta(seconds=dep["dep_sec"])
                if start <= dep_dt <= end:
                    rows.append(dict(dep, day=day, origin=origin, dep_dt=dep_dt))
        rows.sort(key=lambda r: r["dep_dt"])
        unique = []
        seen = set()
        for row in rows:
            key = (row["trip_id"], row["day"])  # one entry per trip run, the earliest boarding
            if key in seen:
                continue
            seen.add(key)
            unique.append(row)
            if len(unique) >= MAX_DEPARTURES:
                break
        return unique


def _usable(search, board, now, deps, wait_min):
    """The departures that can be reached on foot in time and leave within wait_min. Loads their trips."""
    usable = []
    for dep in deps:
        reach = now + timedelta(minutes=board[dep["stop_id"]]["walk_min"])
        if reach <= dep["dep_dt"] <= reach + timedelta(minutes=wait_min):
            usable.append(dep)
    search.load_trips(d["trip_id"] for d in usable)
    return usable


def _board_departures(search, board, now, wait_min=MAX_WAIT_MIN):
    """Usable departures from the boarding stops: reachable on foot in time, leaving within wait_min."""
    earliest = now + timedelta(minutes=min(s["walk_min"] for s in board.values()))
    latest = now + timedelta(minutes=max(s["walk_min"] for s in board.values()) + wait_min)
    return _usable(search, board, now, search.departures(list(board), earliest, latest), wait_min)


def _later_batches(search, board, now, batches=None):
    """Usable departures over the next 24 hours, MAX_DEPARTURES trips at a time, earliest first.

    Each batch starts one second after the last departure of the batch before, so no trip is read
    twice and a busy stop cannot hide a later route behind its first 40 trips.
    """
    start = now + timedelta(minutes=min(s["walk_min"] for s in board.values()))
    latest = now + timedelta(minutes=max(s["walk_min"] for s in board.values()) + LATER_WAIT_MIN)
    for _batch in range(LATER_BATCHES if batches is None else batches):
        search.check_time()
        deps = search.departures(list(board), start, latest)
        if not deps:
            return
        yield _usable(search, board, now, deps, LATER_WAIT_MIN)
        start = max(dep["dep_dt"] for dep in deps) + timedelta(seconds=1)
        if start > latest:
            return


def _buses_running(search, board, now):
    """True when any trip leaves the boarding stops within the normal wait window (route or not)."""
    earliest = now + timedelta(minutes=min(s["walk_min"] for s in board.values()))
    latest = now + timedelta(minutes=max(s["walk_min"] for s in board.values()) + MAX_WAIT_MIN)
    return bool(search.departures(list(board), earliest, latest))


def _leg(dep, board_name, alight_name, arrive, rides):
    """One ride as the result dict describes it."""
    return {
        "route": dep["route"],
        "headsign": dep["headsign"],
        "board_stop": board_name,
        "alight_stop": alight_name,
        "departs": _hhmm(dep["dep_dt"]),
        "arrives": _hhmm(arrive),
        "stops": rides,
    }


def _direct(search, board, alight, now, wait_min=MAX_WAIT_MIN):
    """The direct ride with the earliest arrival (final walk included), or None."""
    return _direct_from(search, board, alight, _board_departures(search, board, now, wait_min))


def _direct_from(search, board, alight, usable):
    """The best direct ride among these usable departures (see _direct), or None."""
    best = None
    for dep in usable:
        if best is not None and dep["dep_dt"] >= best["total"]:
            break  # later departures cannot arrive before the best one
        for rides, stop in search.riding(dep):
            walk = alight.get(stop["stop_id"]) if stop["drop_off"] else None
            if walk is None:
                continue
            arrive = dep["origin"] + timedelta(seconds=stop["arr_sec"])
            total = arrive + timedelta(minutes=walk["walk_min"])
            if best is None or total < best["total"]:
                best = {
                    "dep": dep,
                    "stop": stop,
                    "rides": rides,
                    "arrive": arrive,
                    "total": total,
                    "walk_from": walk["walk_min"],
                }
    if best is None:
        return None
    dep = best["dep"]
    return {
        "legs": [_leg(dep, board[dep["stop_id"]]["name"], best["stop"]["name"], best["arrive"], best["rides"])],
        "walk_to_min": board[dep["stop_id"]]["walk_min"],
        "walk_from_min": best["walk_from"],
        "arrive_by": _hhmm(best["total"]),
    }


def _one_transfer(search, board, alight, now, wait_min=MAX_WAIT_MIN):
    """The best route with exactly one transfer, or None.

    The first bus may leave within wait_min; the second bus must leave within MAX_WAIT_MIN
    of the connection. Only the first buses that are usable (capped) are expanded. For each
    stop they reach, keep the earliest arrival. Then find the second bus that leaves at least
    TRANSFER_MIN_MIN later and reaches the destination.
    """
    return _transfer_from(search, board, alight, _board_departures(search, board, now, wait_min))


def _transfer_from(search, board, alight, usable):
    """The best one-transfer route whose first bus is one of these usable departures (see _one_transfer)."""
    firsts = {}
    for dep in usable:
        for rides, stop in search.riding(dep):
            if not stop["drop_off"]:
                continue
            arrive = dep["origin"] + timedelta(seconds=stop["arr_sec"])
            held = firsts.get(stop["stop_id"])
            if held is None or arrive < held["arrive"]:
                firsts[stop["stop_id"]] = {"dep": dep, "stop": stop, "rides": rides, "arrive": arrive}
    if not firsts:
        return None

    ready = [f["arrive"] + timedelta(minutes=TRANSFER_MIN_MIN) for f in firsts.values()]
    second_deps = search.departures(list(firsts), min(ready), max(ready) + timedelta(minutes=MAX_WAIT_MIN))
    search.load_trips(d["trip_id"] for d in second_deps)
    best = None
    for dep in second_deps:
        first = firsts[dep["stop_id"]]
        if dep["trip_id"] == first["dep"]["trip_id"]:
            continue
        earliest = first["arrive"] + timedelta(minutes=TRANSFER_MIN_MIN)
        if not earliest <= dep["dep_dt"] <= earliest + timedelta(minutes=MAX_WAIT_MIN):
            continue
        if best is not None and dep["dep_dt"] >= best["total"]:
            break
        for rides, stop in search.riding(dep):
            walk = alight.get(stop["stop_id"]) if stop["drop_off"] else None
            if walk is None:
                continue
            arrive = dep["origin"] + timedelta(seconds=stop["arr_sec"])
            total = arrive + timedelta(minutes=walk["walk_min"])
            if best is None or total < best["total"]:
                best = {
                    "first": first,
                    "dep": dep,
                    "stop": stop,
                    "rides": rides,
                    "arrive": arrive,
                    "total": total,
                    "walk_from": walk["walk_min"],
                }
    if best is None:
        return None
    first = best["first"]
    return {
        "legs": [
            _leg(
                first["dep"],
                board[first["dep"]["stop_id"]]["name"],
                first["stop"]["name"],
                first["arrive"],
                first["rides"],
            ),
            _leg(best["dep"], first["stop"]["name"], best["stop"]["name"], best["arrive"], best["rides"]),
        ],
        "walk_to_min": board[first["dep"]["stop_id"]]["walk_min"],
        "walk_from_min": best["walk_from"],
        "arrive_by": _hhmm(best["total"]),
    }


def _later_search(search, board, alight, now):
    """The first ride in the next 24 hours, read a batch at a time: a direct ride first, then one transfer."""
    for usable in _later_batches(search, board, now):
        found = _direct_from(search, board, alight, usable)
        if found is not None:
            return found
    for usable in _later_batches(search, board, now, LATER_TRANSFER_BATCHES):
        found = _transfer_from(search, board, alight, usable)
        if found is not None:
            return found
    return None


def directions(from_lat, from_lng, to_lat, to_lng, when=None, source=None):
    """The soonest-arriving bus route between two points, direct rides first.

    Returns {"legs": [...], "walk_to_min", "walk_from_min", "arrive_by"} or None when no
    ride is found. When nothing leaves within MAX_WAIT_MIN, the first ride in the next
    LATER_WAIT_MIN is returned instead, with "later": True. `source` is any GtfsSource; by
    default the database or the local file. The source is closed when the search ends.
    """
    if source is None:
        source = _default_source()
    if source is None:
        return None
    try:
        now = _as_utc(when)
        board = _near(source, from_lat, from_lng)
        alight = _near(source, to_lat, to_lng)
        if not board or not alight:
            return None
        search = _Search(source)
        direct = _direct(search, board, alight, now)
        if direct is not None:
            return direct
        transfer = _one_transfer(search, board, alight, now)
        if transfer is not None:
            return transfer
        # Nothing leaves within the wait limit (late at night, say): the first ride in the next 24 hours.
        later = _later_search(search, board, alight, now)
        if later is not None:
            later["later"] = True
            later["buses_running"] = _buses_running(search, board, now)
        return later
    except _SearchTimeout:
        print(f"Transit directions took over {TIME_BUDGET_SEC:.0f}s, giving up")
        return None
    except Exception as problem:
        print(f"Transit directions failed: {problem}")
        return None
    finally:
        source.close()


def _nearest_stop(source, lat, lng):
    """The stop closest to a point (within NEAREST_STOP_MILES), or None."""
    stops = source.stops_near(lat, lng, NEAREST_STOP_MILES) or []
    if not stops:
        return None
    return min(stops, key=lambda s: haversine_miles(lat, lng, s["lat"], s["lng"]))


def closest_stops(from_lat, from_lng, to_lat, to_lng, language, channel, source=None):
    """The names of the stops nearest the start and nearest the destination, for a reply with no ride.

    Returns (start stop name, destination stop name), formatted for the channel, or None when either
    point has no stop nearby or there is no schedule. Never raises.
    """
    if source is None:
        source = _default_source()
    if source is None:
        return None
    lang = language if language in _AT else "en"
    sms = channel == "sms"
    try:
        start = _nearest_stop(source, from_lat, from_lng)
        end = _nearest_stop(source, to_lat, to_lng)
        if start is None or end is None:
            return None
        return _stop_name(start["name"], lang, sms), _stop_name(end["name"], lang, sms)
    except Exception as problem:
        print(f"Nearest bus stops failed: {problem}")
        return None
    finally:
        source.close()


# ---------------------------------------------------------------------------
# Speech and text templates. Fixed wording in five languages, no Gemini.
# ---------------------------------------------------------------------------

_KIOSK = {
    "en": {
        "one": "Walk {walk_to} to {board} and take bus {route} toward {headsign} {dep_at}, "
               "getting off at {alight} after {stops}, then walk {walk_from} to your destination.",
        "first": "Walk {walk_to} to {board} and take bus {route} toward {headsign} {dep_at}, "
                 "getting off at {alight} after {stops}.",
        "last": "Transfer to bus {route} toward {headsign} {dep_at} from {board}, getting off at "
                "{alight} after {stops}, then walk {walk_from} to your destination.",
    },
    "es": {
        "one": "Camine {walk_to} hasta {board} y tome el autobús {route} hacia {headsign} {dep_at}, "
               "bájese en {alight} después de {stops} y luego camine {walk_from} a su destino.",
        "first": "Camine {walk_to} hasta {board} y tome el autobús {route} hacia {headsign} {dep_at}, "
                 "bájese en {alight} después de {stops}.",
        "last": "Transborde al autobús {route} hacia {headsign} {dep_at} en {board}, bájese en {alight} "
                "después de {stops} y luego camine {walk_from} a su destino.",
    },
    "vi": {
        "one": "Đi bộ {walk_to} đến {board}, đi xe buýt {route} hướng {headsign} {dep_at}, xuống tại "
               "{alight} sau {stops}, rồi đi bộ {walk_from} đến nơi cần đến.",
        "first": "Đi bộ {walk_to} đến {board}, đi xe buýt {route} hướng {headsign} {dep_at}, xuống tại "
                 "{alight} sau {stops}.",
        "last": "Đổi sang xe buýt {route} hướng {headsign} {dep_at} tại {board}, xuống tại {alight} sau "
                "{stops}, rồi đi bộ {walk_from} đến nơi cần đến.",
    },
    "zh": {
        "one": "步行{walk_to}到{board}，乘坐{route}路公交车（开往{headsign}），{dep_at}上车，在{alight}下车，"
               "经过{stops}后步行{walk_from}到达目的地。",
        "first": "步行{walk_to}到{board}，乘坐{route}路公交车（开往{headsign}），{dep_at}上车，在{alight}下车，"
                 "经过{stops}。",
        "last": "在{board}换乘{route}路公交车（开往{headsign}），{dep_at}上车，在{alight}下车，经过{stops}后步行"
                "{walk_from}到达目的地。",
    },
    "ar": {
        "one": "امشِ {walk_to} إلى {board} ثم اركب الحافلة {route} باتجاه {headsign} {dep_at}، "
               "وانزل في {alight} بعد {stops}، ثم امشِ {walk_from} إلى وجهتك.",
        "first": "امشِ {walk_to} إلى {board} ثم اركب الحافلة {route} باتجاه {headsign} {dep_at}، "
                 "وانزل في {alight} بعد {stops}.",
        "last": "انتقل إلى الحافلة {route} باتجاه {headsign} {dep_at} من {board}، وانزل في {alight} "
                "بعد {stops}، ثم امشِ {walk_from} إلى وجهتك.",
    },
}

# SMS: one line, starting with the first bus, ending with the walk from the last stop.
_SMS = {
    "en": {"leg": "bus {route} from {board} {dep}, {stops} to {alight}", "walk": "walk {walk}",
           "sep": ", ", "end": "."},
    "es": {"leg": "bus {route} desde {board} {dep}, {stops} hasta {alight}", "walk": "caminar {walk}",
           "sep": ", ", "end": "."},
    "vi": {"leg": "xe buýt {route} từ {board} {dep}, {stops} đến {alight}", "walk": "đi bộ {walk}",
           "sep": "; ", "end": "."},
    "zh": {"leg": "{route}路公交{board}{dep}上车，坐{stops}到{alight}", "walk": "步行{walk}",
           "sep": "，", "end": "。"},
    "ar": {"leg": "الحافلة {route} من {board} {dep}، {stops} حتى {alight}", "walk": "امشِ {walk}",
           "sep": "، ", "end": "."},
}

_NO_BUS = {
    "en": {"kiosk": "I did not find a direct bus from here in the next hour and a half.",
           "sms": "No direct bus found."},
    "es": {"kiosk": "No encontré un autobús directo desde aquí en la próxima hora y media.",
           "sms": "No hay autobús directo."},
    "vi": {"kiosk": "Tôi không tìm thấy xe buýt trực tiếp từ đây trong một tiếng rưỡi tới.",
           "sms": "Không có xe buýt trực tiếp."},
    "zh": {"kiosk": "一个半小时内从这里没有找到直达公交车。", "sms": "没有找到直达公交车。"},
    "ar": {"kiosk": "لم أجد حافلة مباشرة من هنا خلال الساعة والنصف القادمة.", "sms": "لا توجد حافلة مباشرة."},
}

_AT = {"en": "at", "es": "en", "vi": "tại", "zh": "和", "ar": "عند"}

# Said before the first ride when no bus leaves the stops in the next 90 minutes ("later": True).
_LATER = {
    "en": "No buses are running now. The first one:",
    "es": "No hay autobuses ahora. El primero:",
    "vi": "Hiện không có xe buýt chạy. Chuyến đầu tiên:",
    "zh": "现在没有公交车运行。第一班车：",
    "ar": "لا توجد حافلات تعمل الآن. الأولى:",
}

# Said before the next ride when buses run but none goes there soon ("later": True, "buses_running": True).
_NEXT_BUS = {
    "en": "The next bus that goes there:",
    "es": "El próximo autobús que va allí:",
    "vi": "Chuyến xe buýt tiếp theo đi đến đó:",
    "zh": "下一班去那里的公交车：",
    "ar": "الحافلة التالية التي تذهب إلى هناك:",
}


def _stops_phrase(language, count):
    """'1 stop', '6 stops' in the language, with each language's plural rules."""
    if language == "en":
        return f"{count} stop" + ("" if count == 1 else "s")
    if language == "es":
        return f"{count} parada" + ("" if count == 1 else "s")
    if language == "vi":
        return f"{count} trạm"
    if language == "zh":
        return f"{count}站"
    if count == 1:
        return "محطة واحدة"
    if count == 2:
        return "محطتان"
    if count <= 10:
        return f"{count} محطات"
    return f"{count} محطة"


def _minutes_phrase(language, count):
    """'1 minute', '3 minutes' in the language (full word, for speech)."""
    if language == "en":
        return f"{count} minute" + ("" if count == 1 else "s")
    if language == "es":
        return f"{count} minuto" + ("" if count == 1 else "s")
    if language == "vi":
        return f"{count} phút"
    if language == "zh":
        return f"{count}分钟"
    if count == 1:
        return "دقيقة"
    if count == 2:
        return "دقيقتان"
    if count <= 10:
        return f"{count} دقائق"
    return f"{count} دقيقة"


def _short_minutes(language, count):
    """Minutes for SMS: '2 min' in English and Spanish, the full word elsewhere."""
    if language in ("en", "es"):
        return f"{count} min"
    return _minutes_phrase(language, count)


def _period(language, hour):
    """The part of the day as a word: AM/PM, a. m./p. m., sáng/chiều/tối, ص/م."""
    if language == "en":
        return "AM" if hour < 12 else "PM"
    if language == "es":
        return "a. m." if hour < 12 else "p. m."
    if language == "vi":
        if hour < 12:
            return "sáng"
        return "chiều" if hour < 18 else "tối"
    return "ص" if hour < 12 else "م"


def _clock(hhmm, language, sms):
    """'16:12' as a 12-hour time, e.g. '4:12 PM' (SMS English is '4:12PM')."""
    hour, minute = (int(part) for part in hhmm.split(":"))
    hour %= 24
    hour12 = hour % 12 or 12
    if language == "zh":
        return f"{'下午' if hour >= 12 else '上午'}{hour12}:{minute:02d}"
    joiner = "" if sms and language == "en" else " "
    return f"{hour12}:{minute:02d}{joiner}{_period(language, hour)}"


def _time_at(hhmm, language):
    """The kiosk phrase for a departure time: 'at 4:12 PM', 'a las 4:12 p. m.', 'a la 1:05 p. m.'."""
    hour = int(hhmm.split(":")[0]) % 24
    clock = _clock(hhmm, language, False)
    if language == "en":
        return f"at {clock}"
    if language == "es":
        return f"{'a la' if hour % 12 == 1 else 'a las'} {clock}"
    if language == "vi":
        return f"lúc {clock}"
    if language == "ar":
        return f"الساعة {clock}"
    return clock  # zh: the time word is attached directly to the verb


def _stop_name(name, language, sms):
    """A stop name for speech: 'Scott @ Holman' becomes 'Scott at Holman' (zh: 'Scott与Holman交叉口').

    SMS keeps the '@' with no spaces: 'Scott@Holman'.
    """
    text = re.sub(r"\s+", " ", name or "").strip()
    if sms:
        return re.sub(r"\s*@\s*", "@", text)
    parts = re.split(r"\s*@\s*", text)
    if language == "zh" and len(parts) == 2:
        return f"{parts[0]}与{parts[1]}交叉口"
    return f" {_AT[language]} ".join(parts)


def _leg_values(leg, language, sms):
    """The values one leg fills into a template."""
    return {
        "route": leg["route"],
        "headsign": leg["headsign"],
        "board": _stop_name(leg["board_stop"], language, sms),
        "alight": _stop_name(leg["alight_stop"], language, sms),
        "dep": _clock(leg["departs"], language, sms),
        "dep_at": _time_at(leg["departs"], language),
        "stops": _stops_phrase(language, leg["stops"]),
    }


def _kiosk_summary(d, language):
    """One spoken sentence per leg."""
    templates = _KIOSK[language]
    legs = d["legs"]
    walks = {
        "walk_to": _minutes_phrase(language, d.get("walk_to_min", 1)),
        "walk_from": _minutes_phrase(language, d.get("walk_from_min", 1)),
    }
    if len(legs) == 1:
        return templates["one"].format(**walks, **_leg_values(legs[0], language, False))
    first = templates["first"].format(**walks, **_leg_values(legs[0], language, False))
    last = templates["last"].format(**walks, **_leg_values(legs[-1], language, False))
    joiner = "" if language == "zh" else " "
    return first + joiner + last


def _sms_summary(d, language):
    """One compact line: each bus, then the walk from the last stop."""
    templates = _SMS[language]
    parts = [templates["leg"].format(**_leg_values(leg, language, True)) for leg in d["legs"]]
    parts.append(templates["walk"].format(walk=_short_minutes(language, d.get("walk_from_min", 1))))
    text = templates["sep"].join(parts) + templates["end"]
    return text[:1].upper() + text[1:]


def summary(d, language, channel):
    """The bus directions as text. channel "kiosk" is spoken; "sms" is compact.

    `d` is the result of directions() (None means no ride was found). Unknown languages use English.
    """
    lang = language if language in _KIOSK else "en"
    sms = channel == "sms"
    if not d or not d.get("legs"):
        return _NO_BUS[lang]["sms" if sms else "kiosk"]
    text = _sms_summary(d, lang) if sms else _kiosk_summary(d, lang)
    if d.get("later"):
        prefix = (_NEXT_BUS if d.get("buses_running") else _LATER)[lang]
        text = prefix + ("" if lang == "zh" else " ") + text
    return text
