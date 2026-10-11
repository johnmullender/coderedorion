"""Database layer for HelpLine Houston.

The real database is Tiger Data (hosted Postgres with TimescaleDB). The connection
URL comes from the DATABASE_URL setting in .env. If that setting is missing, or the
database cannot be reached, everything here quietly uses memory instead, so the
kiosk demo still works offline. Nothing in this file should ever crash the app.
"""

import os
import pathlib
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

# Read the .env file that sits next to this file (if there is one).
load_dotenv(pathlib.Path(__file__).resolve().parent / ".env")

HOUSTON = ZoneInfo("America/Chicago")
RESOURCE_CACHE_SECONDS = 60      # how long we remember the resources we read from the DB
DOWN_SECONDS = 30                # after the DB host fails to connect, skip it this long
MEMORY_LIMIT = 500               # how many request logs we keep in memory
DEFAULT_LIMIT = 500

# In-memory fallback for the request log (used when there is no database).
_memory_requests = []
_next_memory_id = 1
_memory_lock = threading.Lock()  # the web server may handle requests on several threads

# Cache for the resource list read from the database.
# "rows" is None when nothing is cached; "at" is when we last read it (seconds).
_resources_cache = {"at": 0.0, "rows": None}

# When the database cannot be reached, we stop trying until this time (monotonic seconds).
_down_until = 0.0
# True while we are in an outage whose warning has already been printed.
_outage_reported = False


def _database_url():
    """Return DATABASE_URL, read fresh each time so tests can change it."""
    return os.environ.get("DATABASE_URL", "").strip()


def enabled():
    """True when a DATABASE_URL is set. It does not check that the database answers."""
    return bool(_database_url())


def _db_ok():
    """True when we should try the database: a URL is set and we are not in a recent outage."""
    return enabled() and time.monotonic() >= _down_until


def _connect():
    """Open a new short-lived connection to Tiger Data. Each call uses its own connection."""
    return psycopg.connect(_database_url(), connect_timeout=5, autocommit=True)


def _mark_down(problem):
    """Remember a failure and print one warning for the whole outage.

    Only a connection failure (psycopg.OperationalError) pauses the database for
    DOWN_SECONDS. A bad SQL statement just falls back for that one call.
    """
    global _down_until, _outage_reported
    if isinstance(problem, psycopg.OperationalError):
        _down_until = time.monotonic() + DOWN_SECONDS
    if not _outage_reported:
        _outage_reported = True
        print(f"Database unavailable, using in-memory storage: {problem}")


def _mark_up():
    """Called after a successful database call: the next outage will print a warning again."""
    global _outage_reported
    _outage_reported = False


def _as_datetime(value):
    """Turn a time into a timezone-aware datetime in Houston time.

    Accepts None (meaning now), a datetime, or an ISO text such as
    "2026-10-10T19:05:00-05:00". A time with no zone is treated as Houston time.
    """
    if value is None:
        return datetime.now(HOUSTON)
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=HOUSTON)
    return value


def _houston_iso(value):
    """Format a time as ISO text in Houston time, with the UTC offset and no microseconds."""
    return _as_datetime(value).astimezone(HOUSTON).replace(microsecond=0).isoformat()


def _request_item(row_id, when, entry):
    """Build one request item in the exact shape the /api/requests output uses."""
    return {
        "id": row_id,
        "time": _houston_iso(when),
        "channel": entry.get("channel"),
        "need": entry.get("need"),
        "language": entry.get("language"),
        "lat": entry.get("lat"),
        "lng": entry.get("lng"),
        "matched": bool(entry.get("matched")),
        "place": entry.get("place"),
        "text": entry.get("text"),
    }


def init_db():
    """Create the tables if they are missing. Called once when the app starts."""
    if not _db_ok():
        return
    try:
        with _connect() as conn:
            # Resources: one row per help place. The whole record is kept in "data".
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS resources (
                    id text PRIMARY KEY,
                    name text,
                    category text[],
                    lat double precision,
                    lng double precision,
                    data jsonb NOT NULL,
                    updated_at timestamptz DEFAULT now()
                )
                """
            )
            # Requests: one row per question asked, used for the dashboard.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS requests (
                    id bigserial,
                    time timestamptz NOT NULL,
                    channel text,
                    need text,
                    language text,
                    lat double precision,
                    lng double precision,
                    matched boolean,
                    place text,
                    text text
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS requests_time_idx ON requests (time)")
        _mark_up()
    except Exception as problem:
        _mark_down(problem)
        return

    # Turn "requests" into a TimescaleDB hypertable (fast time-based queries).
    # Newer TimescaleDB uses by_range('time'); older versions take the column name.
    # Each try uses its own connection. Plain Postgres (local tests) has neither,
    # and that is fine.
    for statement in (
        "SELECT create_hypertable('requests', by_range('time'), if_not_exists => TRUE)",
        "SELECT create_hypertable('requests', 'time', if_not_exists => TRUE)",
    ):
        try:
            with _connect() as conn:
                conn.execute(statement)
            return
        except Exception:
            continue
    print("TimescaleDB not used")


def get_resources():
    """Return all resource records as a list of dicts.

    Uses the database when it is enabled and has rows (cached for 60 seconds).
    Otherwise uses the resources.json file through matching.load_resources().
    """
    if _db_ok():
        cached = _resources_cache["rows"]
        if cached is not None and time.monotonic() - _resources_cache["at"] < RESOURCE_CACHE_SECONDS:
            return cached
        try:
            with _connect() as conn:
                rows = [row[0] for row in conn.execute("SELECT data FROM resources ORDER BY id")]
            _mark_up()
            if rows:
                _resources_cache["rows"] = rows
                _resources_cache["at"] = time.monotonic()
                return rows
            print("Resources table is empty, using data/resources.json instead")
        except Exception as problem:
            _mark_down(problem)

    # Import here so this module can load even if matching.py has a problem.
    import matching

    return matching.load_resources()


def upsert_resources(rows, remove_others=False):
    """Insert the resource records, or update them if the id already exists.

    With remove_others=True, any place in the table that is not in `rows` is deleted.

    Returns how many rows were saved. Returns 0 if there is no database.
    """
    global _resources_cache
    if not _db_ok() or not rows:
        return 0
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO resources (id, name, category, lat, lng, data, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (id) DO UPDATE SET
                        name = EXCLUDED.name,
                        category = EXCLUDED.category,
                        lat = EXCLUDED.lat,
                        lng = EXCLUDED.lng,
                        data = EXCLUDED.data,
                        updated_at = now()
                    """,
                    [
                        (
                            row["id"],
                            row.get("name"),
                            list(row.get("category") or []),
                            row.get("lat"),
                            row.get("lng"),
                            Jsonb(row),
                        )
                        for row in rows
                    ],
                )
                if remove_others:
                    # Places no longer in the file (e.g. the old samples) are removed.
                    cur.execute("DELETE FROM resources WHERE NOT (id = ANY(%s))",
                                ([row["id"] for row in rows],))
        _mark_up()
        # The table changed, so forget the cached copy.
        _resources_cache = {"at": 0.0, "rows": None}
        return len(rows)
    except Exception as problem:
        _mark_down(problem)
        return 0


def log_request(entry):
    """Save one request. Returns it as a dict with its new "id" added.

    "entry" has the request fields: channel, need, language, lat, lng, matched,
    place, text, and optionally time (defaults to now).
    """
    global _next_memory_id
    try:
        when = _as_datetime(entry.get("time"))
    except Exception:
        when = datetime.now(HOUSTON)  # a bad time should not stop the log

    if _db_ok():
        try:
            with _connect() as conn:
                row = conn.execute(
                    """
                    INSERT INTO requests
                        (time, channel, need, language, lat, lng, matched, place, text)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        when,
                        entry.get("channel"),
                        entry.get("need"),
                        entry.get("language"),
                        entry.get("lat"),
                        entry.get("lng"),
                        bool(entry.get("matched")),
                        entry.get("place"),
                        entry.get("text"),
                    ),
                ).fetchone()
            _mark_up()
            return _request_item(row[0], when, entry)
        except Exception as problem:
            _mark_down(problem)

    # Fallback: keep the request in memory.
    with _memory_lock:
        item = _request_item(_next_memory_id, when, entry)
        _next_memory_id += 1
        _memory_requests.append(item)
        if len(_memory_requests) > MEMORY_LIMIT:
            del _memory_requests[: len(_memory_requests) - MEMORY_LIMIT]
    return dict(item)


def recent_requests(limit=DEFAULT_LIMIT):
    """Return the last `limit` requests, oldest first (newest last)."""
    try:
        limit = max(0, int(limit))
    except Exception:
        limit = DEFAULT_LIMIT
    if limit == 0:
        return []

    if _db_ok():
        try:
            with _connect() as conn:
                # Take the newest rows first, then flip them so the oldest comes first.
                rows = conn.execute(
                    """
                    SELECT id, time, channel, need, language, lat, lng, matched, place, text
                    FROM requests
                    ORDER BY time DESC, id DESC
                    LIMIT %s
                    """,
                    (limit,),
                ).fetchall()
            _mark_up()
            items = []
            for row in reversed(rows):
                entry = {
                    "channel": row[2], "need": row[3], "language": row[4],
                    "lat": row[5], "lng": row[6], "matched": row[7],
                    "place": row[8], "text": row[9],
                }
                items.append(_request_item(row[0], row[1], entry))
            return items
        except Exception as problem:
            _mark_down(problem)

    # Fallback: the in-memory list (already oldest first).
    with _memory_lock:
        return [dict(item) for item in _memory_requests[-limit:]]
