"""Load the METRO GTFS schedule into Tiger Data, or write a small local copy for the kiosk.

Run from the repo root:

  python3 scripts/load_gtfs.py                          # download the METRO zip, load gtfs_* tables
  python3 scripts/load_gtfs.py path/to/metro-gtfs.zip   # same, from a zip you already have
  python3 scripts/load_gtfs.py --local 29.7199 -95.3422 # write data/transit_local.json near that point

Database mode needs DATABASE_URL (environment, or the .env file next to this folder).
It drops and recreates the gtfs_* tables inside one transaction, so readers see the
old schedule until the new one commits. Uses only the standard library, httpx and psycopg.
"""

import argparse
import csv
import io
import json
import math
import os
import pathlib
import sys
import tempfile
import zipfile
from datetime import datetime, timezone

import httpx
import psycopg
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
load_dotenv(ENV_FILE)
LOCAL_OUT = ROOT / "data" / "transit_local.json"
GTFS_URL = "https://metro.resourcespace.com/pages/download.php?ref=4835&ext=zip"
LOCAL_MILES = 0.5
EARTH_RADIUS_MI = 3958.8
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

# Each table: the GTFS file it comes from, its columns (name, SQL type) and its primary key.
TABLES = {
    "gtfs_stops": {
        "file": "stops.txt", "required": True, "primary": "stop_id",
        "columns": [
            ("stop_id", "text"), ("stop_code", "text"), ("stop_name", "text"), ("stop_desc", "text"),
            ("stop_lat", "double precision"), ("stop_lon", "double precision"),
            ("location_type", "integer"), ("parent_station", "text"), ("wheelchair_boarding", "integer"),
        ],
    },
    "gtfs_routes": {
        "file": "routes.txt", "required": True, "primary": "route_id",
        "columns": [
            ("route_id", "text"), ("agency_id", "text"), ("route_short_name", "text"),
            ("route_long_name", "text"), ("route_type", "integer"),
        ],
    },
    "gtfs_trips": {
        "file": "trips.txt", "required": True, "primary": "trip_id",
        "columns": [
            ("trip_id", "text"), ("route_id", "text"), ("service_id", "text"), ("trip_headsign", "text"),
            ("direction_id", "integer"), ("block_id", "text"), ("shape_id", "text"),
        ],
    },
    "gtfs_stop_times": {
        "file": "stop_times.txt", "required": True, "primary": None,
        "columns": [
            ("trip_id", "text"), ("arrival", "integer"), ("departure", "integer"), ("stop_id", "text"),
            ("stop_sequence", "integer"), ("pickup_type", "integer"), ("drop_off_type", "integer"),
        ],
    },
    "gtfs_calendar": {
        "file": "calendar.txt", "required": False, "primary": "service_id",
        "columns": [("service_id", "text")] + [(day, "boolean") for day in WEEKDAYS]
        + [("start_date", "date"), ("end_date", "date")],
    },
    "gtfs_calendar_dates": {
        "file": "calendar_dates.txt", "required": False, "primary": None,
        "columns": [("service_id", "text"), ("date", "date"), ("exception_type", "integer")],
    },
}

INDEXES = [
    "CREATE INDEX gtfs_stop_times_stop_departure_idx ON gtfs_stop_times (stop_id, departure)",
    "CREATE INDEX gtfs_stop_times_trip_seq_idx ON gtfs_stop_times (trip_id, stop_sequence)",
    "CREATE INDEX gtfs_stops_lat_lon_idx ON gtfs_stops (stop_lat, stop_lon)",
    "CREATE INDEX gtfs_trips_service_idx ON gtfs_trips (service_id)",
    'CREATE INDEX gtfs_calendar_dates_date_idx ON gtfs_calendar_dates ("date")',
]


class Feed:
    """Reads the text files inside a GTFS zip without extracting anything to disk."""

    def __init__(self, archive):
        self._archive = archive
        self._members = {}
        for info in archive.infolist():
            base = info.filename.rsplit("/", 1)[-1]
            if not info.is_dir():
                self._members.setdefault(base, info.filename)

    def rows(self, name, required=False):
        """Each row of a GTFS file as a dict of stripped strings. A missing optional file gives no rows."""
        member = self._members.get(name)
        if member is None:
            if required:
                raise ValueError(f"{name} is missing from the GTFS zip")
            return
        with self._archive.open(member) as raw, io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as text:
            for row in csv.DictReader(text):
                yield {key.strip(): (value or "").strip() for key, value in row.items() if key}


def _seconds(text):
    """'16:12:00' or '25:10:00' as seconds after midnight. Empty text gives None."""
    if not text:
        return None
    hours, minutes, seconds = text.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds)


def _parse(value, kind):
    """One CSV value converted to the column type. Empty values become None."""
    if value is None or value == "":
        return None
    if kind == "integer":
        return int(value)
    if kind == "double precision":
        return float(value)
    if kind == "date":
        return datetime.strptime(value, "%Y%m%d").date()
    if kind == "boolean":
        return value == "1"
    return value


def _stop_time_row(raw):
    """A stop_times row as (trip_id, arrival, departure, stop_id, stop_sequence, pickup, drop_off).

    A missing arrival or departure is copied from the other one. If both are missing the
    value is None here and _fill_trip works it out from the neighbouring stops.
    """
    arrival = _seconds(raw.get("arrival_time"))
    departure = _seconds(raw.get("departure_time"))
    if arrival is None:
        arrival = departure
    if departure is None:
        departure = arrival
    return (
        raw["trip_id"],
        arrival,
        departure,
        raw["stop_id"],
        int(raw["stop_sequence"]),
        _parse(raw.get("pickup_type"), "integer"),
        _parse(raw.get("drop_off_type"), "integer"),
    )


def _interpolate(values):
    """Fill None entries in a list of times: straight-line between the nearest known times.

    Missing entries before the first or after the last known time copy that known time.
    """
    out = list(values)
    known = [i for i, v in enumerate(values) if v is not None]
    if not known:
        return out
    for k in range(len(values)):
        if out[k] is not None:
            continue
        before = max((i for i in known if i < k), default=None)
        after = min((i for i in known if i > k), default=None)
        if before is None:
            out[k] = values[after]
        elif after is None:
            out[k] = values[before]
        else:
            share = (k - before) / (after - before)
            out[k] = round(values[before] + (values[after] - values[before]) * share)
    return out


def _fill_trip(rows):
    """Put one trip's stop_times rows in order and fill in missing times.

    Rows where no time could be worked out (a trip with no timed stops at all) are dropped.
    """
    rows = sorted(rows, key=lambda r: r[4])
    arrivals = _interpolate([r[1] for r in rows])
    departures = _interpolate([r[2] for r in rows])
    filled = []
    for row, arrival, departure in zip(rows, arrivals, departures):
        if arrival is None or departure is None:
            continue
        filled.append((row[0], arrival, departure, row[3], row[4], row[5], row[6]))
    return filled


def _stop_time_rows(feed):
    """All stop_times rows with missing times filled in, one trip at a time.

    Rows of one trip must sit next to each other in stop_times.txt (they do in METRO's feed).
    """
    group = []
    for raw in feed.rows("stop_times.txt", required=True):
        row = _stop_time_row(raw)
        if group and group[0][0] != row[0]:
            yield from _fill_trip(group)
            group = []
        group.append(row)
    if group:
        yield from _fill_trip(group)


def _table_rows(feed, table, spec):
    """The rows to COPY into one table, already converted."""
    if table == "gtfs_stop_times":
        yield from _stop_time_rows(feed)
        return
    for raw in feed.rows(spec["file"], spec["required"]):
        yield tuple(_parse(raw.get(name), kind) for name, kind in spec["columns"])


def _create_sql(table, spec):
    """CREATE TABLE statement for one gtfs_* table."""
    columns = []
    for name, kind in spec["columns"]:
        suffix = " PRIMARY KEY" if name == spec["primary"] else ""
        columns.append(f'"{name}" {kind}{suffix}')
    return f"CREATE TABLE {table} ({', '.join(columns)})"


def _copy(cursor, feed, table, spec):
    """Bulk load one table with COPY. Returns the number of rows written."""
    names = ", ".join(f'"{name}"' for name, _ in spec["columns"])
    count = 0
    with cursor.copy(f"COPY {table} ({names}) FROM STDIN") as copy:
        for row in _table_rows(feed, table, spec):
            copy.write_row(row)
            count += 1
    return count


def database_url():
    """DATABASE_URL from the environment. The .env file next to the repo root was loaded at import."""
    return os.environ.get("DATABASE_URL", "").strip()


def load_database(feed, url):
    """Recreate the gtfs_* tables and load the feed, all in one transaction."""
    with psycopg.connect(url, connect_timeout=10) as conn:
        with conn.cursor() as cursor:
            for table in TABLES:
                cursor.execute(f"DROP TABLE IF EXISTS {table}")
            for table, spec in TABLES.items():
                cursor.execute(_create_sql(table, spec))
            for table, spec in TABLES.items():
                count = _copy(cursor, feed, table, spec)
                print(f"{table}: {count} rows")
            for statement in INDEXES:
                cursor.execute(statement)
            cursor.execute("ANALYZE")
    print("Schedule loaded.")


def _miles_between(lat1, lng1, lat2, lng2):
    """Straight-line distance in miles (same formula as matching.haversine_miles)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lam = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lam / 2) ** 2
    return 2 * EARTH_RADIUS_MI * math.asin(math.sqrt(a))


def write_local(feed, lat, lng, out_path=LOCAL_OUT, miles=LOCAL_MILES):
    """Write the stops within `miles` of a point, every trip that serves them with its full stop list.

    The file also lists every stop those trips serve, so destinations farther away can still be found.
    """
    stops = {}
    for raw in feed.rows("stops.txt", required=True):
        if raw.get("stop_lat") and raw.get("stop_lon"):
            stops[raw["stop_id"]] = {
                "name": raw.get("stop_name", ""),
                "lat": float(raw["stop_lat"]),
                "lng": float(raw["stop_lon"]),
            }
    near = {
        stop_id for stop_id, s in stops.items() if _miles_between(lat, lng, s["lat"], s["lng"]) <= miles
    }

    trip_ids = {
        raw["trip_id"]
        for raw in feed.rows("stop_times.txt", required=True)
        if raw["stop_id"] in near
    }
    by_trip = {}
    for raw in feed.rows("stop_times.txt", required=True):
        if raw["trip_id"] in trip_ids:
            by_trip.setdefault(raw["trip_id"], []).append(_stop_time_row(raw))

    trip_meta = {raw["trip_id"]: raw for raw in feed.rows("trips.txt", required=True) if raw["trip_id"] in trip_ids}
    routes = {
        raw["route_id"]: raw.get("route_short_name") or raw["route_id"]
        for raw in feed.rows("routes.txt", required=True)
    }

    trips = []
    used_stops = {}
    services = set()
    for trip_id in sorted(by_trip):
        meta = trip_meta.get(trip_id)
        if meta is None:
            continue
        stop_list = []
        for _, arrival, departure, stop_id, sequence, pickup, drop_off in _fill_trip(by_trip[trip_id]):
            stop = stops.get(stop_id)
            if stop is None:
                continue
            used_stops[stop_id] = stop
            stop_list.append({
                "stop_id": stop_id,
                "name": stop["name"],
                "stop_sequence": sequence,
                "arrival": arrival,
                "departure": departure,
                "lat": stop["lat"],
                "lng": stop["lng"],
                "pickup_type": pickup or 0,
                "drop_off_type": drop_off or 0,
            })
        if not stop_list:
            continue
        services.add(meta["service_id"])
        trips.append({
            "trip_id": trip_id,
            "route": routes.get(meta["route_id"], meta["route_id"]),
            "headsign": meta.get("trip_headsign", ""),
            "service_id": meta["service_id"],
            "stops": stop_list,
        })

    calendar = [
        {
            "service_id": raw["service_id"],
            "days": [_parse(raw.get(day), "boolean") is True for day in WEEKDAYS],
            "start_date": _parse(raw["start_date"], "date").isoformat(),
            "end_date": _parse(raw["end_date"], "date").isoformat(),
        }
        for raw in feed.rows("calendar.txt")
        if raw["service_id"] in services
    ]
    calendar_dates = [
        {
            "service_id": raw["service_id"],
            "date": _parse(raw["date"], "date").isoformat(),
            "exception_type": int(raw["exception_type"]),
        }
        for raw in feed.rows("calendar_dates.txt")
        if raw["service_id"] in services
    ]

    data = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "center": {"lat": lat, "lng": lng},
        "miles": miles,
        "stops": [
            {"stop_id": stop_id, "name": s["name"], "lat": s["lat"], "lng": s["lng"]}
            for stop_id, s in sorted(used_stops.items())
        ],
        "trips": trips,
        "calendar": calendar,
        "calendar_dates": calendar_dates,
    }
    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {out_path}: {len(near)} stops within {miles} mi, {len(trips)} trips.")


def download(url):
    """Download the zip into a new empty folder. Returns the path of the zip."""
    path = pathlib.Path(tempfile.mkdtemp(prefix="gtfs-")) / "metro-gtfs.zip"
    print(f"Downloading {url}")
    with httpx.stream("GET", url, follow_redirects=True, timeout=120) as response:
        response.raise_for_status()
        with open(path, "wb") as out:
            for chunk in response.iter_bytes():
                out.write(chunk)
    return path


def fetch_zip(source):
    """A local zip path as given, or the download of a URL. Exits if it is not a zip."""
    path = pathlib.Path(source)
    if not path.is_file():
        path = download(source)
    if not zipfile.is_zipfile(path):
        sys.exit(f"Not a zip file: {path}")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Load METRO GTFS into Tiger Data, or write data/transit_local.json.")
    parser.add_argument("source", nargs="?", default=GTFS_URL, help="GTFS zip URL or local zip path")
    parser.add_argument(
        "--local", nargs=2, type=float, metavar=("LAT", "LNG"),
        help="write data/transit_local.json for stops near this point instead of loading the database",
    )
    args = parser.parse_args(argv)

    zip_path = fetch_zip(args.source)
    with zipfile.ZipFile(zip_path) as archive:
        feed = Feed(archive)
        if args.local:
            write_local(feed, args.local[0], args.local[1])
            return 0
        url = database_url()
        if not url:
            print("DATABASE_URL is not set (environment or .env).")
            return 1
        load_database(feed, url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
