"""Tests for db.py.

Two kinds of tests:
- In-memory fallback tests: DATABASE_URL is removed, so db.py uses memory.
- Local database tests: use a local Postgres 16 (no TimescaleDB). If that
  server is not running, these tests are skipped instead of failing.
"""

import pathlib
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import matching  # noqa: E402

LOCAL_URL = "postgresql://postgres@/helpline_test?host=/tmp&port=5433"
DEAD_URL = "postgresql://postgres@localhost:5999/helpline_test"
HOUSTON = ZoneInfo("America/Chicago")


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    """Start every test with an empty in-memory log and an empty resource cache."""
    db._memory_requests.clear()
    db._next_memory_id = 1
    db._resources_cache["rows"] = None
    db._down_until = 0.0
    db._outage_reported = False
    monkeypatch.delenv("DATABASE_URL", raising=False)
    yield


@pytest.fixture
def local_db(monkeypatch):
    """Point DATABASE_URL at the local test database, or skip if it is not reachable."""
    monkeypatch.setenv("DATABASE_URL", LOCAL_URL)
    try:
        db._connect().close()
    except Exception as problem:
        pytest.skip(f"local test Postgres not reachable: {problem}")
    db._down_until = 0.0
    db.init_db()
    # Clean slate for every test.
    with db._connect() as conn:
        conn.execute("TRUNCATE requests, resources")
    db._resources_cache["rows"] = None
    yield


def make_resource(resource_id, name="Test Pantry", category=None):
    """A small resource record in the same shape as data/resources.json."""
    return {
        "id": resource_id,
        "name": name,
        "category": category or ["food"],
        "address": "1 Test St, Houston, TX 77004",
        "lat": 29.72,
        "lng": -95.36,
        "hours": {day: ["00:00-24:00"] for day in ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]},
        "requirements": {"id_required": False, "proof_of_income": False, "zip_restricted": []},
        "languages": ["en"],
        "phone": "713-555-0100",
        "notes": "",
        "source_url": "",
        "last_verified": None,
    }


def make_entry(text, time=None, need="food", matched=True):
    """A request entry as main.py would log it."""
    entry = {
        "channel": "sms",
        "need": need,
        "language": "en",
        "lat": 29.72,
        "lng": -95.35,
        "matched": matched,
        "place": "Test Pantry" if matched else None,
        "text": text,
    }
    if time is not None:
        entry["time"] = time
    return entry


REQUEST_KEYS = {"id", "time", "channel", "need", "language", "lat", "lng", "matched", "place", "text"}


# ---------- In-memory fallback (no DATABASE_URL) ----------

def test_enabled_false_without_url():
    assert db.enabled() is False


def test_init_db_does_nothing_without_url():
    db.init_db()  # must not raise


def test_fallback_resources_come_from_json_file():
    rows = db.get_resources()
    assert [r["id"] for r in rows] == [r["id"] for r in matching.load_resources()]


def test_fallback_upsert_returns_zero_and_does_not_raise():
    assert db.upsert_resources([make_resource("x")]) == 0


def test_fallback_log_request_and_recent_requests():
    first = db.log_request(make_entry("comida", time="2026-10-10T19:05:00-05:00"))
    second = db.log_request(make_entry("comida 2", time="2026-10-10T19:06:00-05:00"))
    assert first["id"] == 1
    assert second["id"] == 2
    items = db.recent_requests()
    assert [item["text"] for item in items] == ["comida", "comida 2"]  # oldest first
    assert set(items[0].keys()) == REQUEST_KEYS
    assert items[0]["time"] == "2026-10-10T19:05:00-05:00"


def test_fallback_memory_is_capped_at_500():
    for i in range(510):
        db.log_request(make_entry(f"req {i}"))
    assert len(db._memory_requests) == 500
    assert db.recent_requests(1)[0]["text"] == "req 509"


def test_fallback_recent_requests_limit():
    for i in range(5):
        db.log_request(make_entry(f"req {i}"))
    assert [item["text"] for item in db.recent_requests(2)] == ["req 3", "req 4"]


# ---------- Dead database URL: must fall back, never raise ----------

def test_dead_port_falls_back_to_memory(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", DEAD_URL)
    assert db.enabled() is True
    db.init_db()  # must not raise
    assert "Database unavailable, using in-memory storage" in capsys.readouterr().out

    entry = db.log_request(make_entry("hola", need="shelter", matched=False))
    assert entry["id"] == 1
    assert db.recent_requests()[-1]["text"] == "hola"

    rows = db.get_resources()  # falls back to the json file
    assert len(rows) == len(matching.load_resources())
    assert db.upsert_resources([make_resource("x")]) == 0


def test_down_database_is_not_retried_for_30_seconds(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", DEAD_URL)
    calls = {"n": 0}

    def failing_connect():
        calls["n"] += 1
        raise psycopg.OperationalError("host unreachable")

    monkeypatch.setattr(db, "_connect", failing_connect)
    for _ in range(3):
        db.log_request(make_entry("x"))
    assert calls["n"] == 1  # later calls skip the database during the outage
    assert capsys.readouterr().out.count("Database unavailable") == 1  # one warning per outage

    db._down_until = 0.0  # pretend the 30 seconds are over
    db.log_request(make_entry("y"))
    assert calls["n"] == 2


# ---------- Local Postgres tests (skipped if not running) ----------

def test_init_db_twice_is_fine(local_db):
    db.init_db()
    db.init_db()


def test_upsert_then_get_resources(local_db):
    db.upsert_resources([make_resource("a", "Alpha"), make_resource("b", "Beta")])
    rows = db.get_resources()
    assert sorted(r["name"] for r in rows) == ["Alpha", "Beta"]
    assert rows[0]["hours"]["mon"] == ["00:00-24:00"]


def test_upsert_updates_existing_id(local_db):
    db.upsert_resources([make_resource("a", "Alpha")])
    assert db.upsert_resources([make_resource("a", "Alpha Renamed", ["shelter"])]) == 1
    rows = db.get_resources()
    assert len(rows) == 1
    assert rows[0]["name"] == "Alpha Renamed"
    assert rows[0]["category"] == ["shelter"]


def test_get_resources_falls_back_to_json_when_table_empty(local_db):
    rows = db.get_resources()
    assert [r["id"] for r in rows] == [r["id"] for r in matching.load_resources()]


def test_get_resources_is_cached(local_db):
    db.upsert_resources([make_resource("a", "Alpha")])
    db.get_resources()
    # Change the table behind the cache's back: the cached copy is still returned.
    with db._connect() as conn:
        conn.execute("UPDATE resources SET data = data || '{\"name\": \"Changed\"}'::jsonb")
    assert db.get_resources()[0]["name"] == "Alpha"


def test_log_request_returns_ids(local_db):
    first = db.log_request(make_entry("uno", time="2026-10-10T19:05:00-05:00"))
    second = db.log_request(make_entry("dos", time="2026-10-10T19:06:00-05:00"))
    assert isinstance(first["id"], int)
    assert second["id"] > first["id"]
    assert first["time"] == "2026-10-10T19:05:00-05:00"


def test_recent_requests_order_and_shape(local_db):
    db.log_request(make_entry("c", time="2026-10-10T19:07:00-05:00"))
    db.log_request(make_entry("a", time="2026-10-10T19:05:00-05:00"))
    db.log_request(make_entry("b", time="2026-10-10T19:06:00-05:00", matched=False))

    items = db.recent_requests()
    assert [item["text"] for item in items] == ["a", "b", "c"]  # oldest first
    for item in items:
        assert set(item.keys()) == REQUEST_KEYS
        assert isinstance(item["matched"], bool)

    assert items[1]["matched"] is False
    assert items[1]["place"] is None

    # Times come back in Houston time with the UTC offset.
    parsed = datetime.fromisoformat(items[0]["time"])
    assert parsed.astimezone(HOUSTON).utcoffset() == timedelta(hours=-5)


def test_recent_requests_limit_keeps_newest(local_db):
    for minute in range(5):
        db.log_request(make_entry(f"m{minute}", time=f"2026-10-10T19:0{minute}:00-05:00"))
    items = db.recent_requests(2)
    assert [item["text"] for item in items] == ["m3", "m4"]
