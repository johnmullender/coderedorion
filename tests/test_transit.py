"""Tests for transit.py and scripts/load_gtfs.py.

No network. FakeSource holds a tiny made-up schedule in memory, LocalSource reads a
temp JSON file, and the loader test builds a small GTFS zip in a temp folder.
"""

import json
import math
import pathlib
import sys
import zipfile
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import load_gtfs  # noqa: E402
import transit  # noqa: E402
from matching import haversine_miles  # noqa: E402

HOUSTON = ZoneInfo("America/Chicago")
ORIGIN = (29.7200, -95.3500)
DEST = (29.7300, -95.3500)
DEAD_URL = "postgresql://postgres@localhost:5999/helpline_test"
MONDAY = date(2026, 10, 12)  # 2026-10-10 is a Saturday, 2026-10-11 Sunday, 2026-10-12 Monday

# name, lat, lng. A is next to the origin, B is next to the destination,
# W is a 4-minute walk from the origin, C is too far from both, M is far away.
STOPS = {
    "A": ("Scott @ Holman", 29.7201, -95.3500),
    "B": ("Main @ Gray", 29.7300, -95.3501),
    "W": ("Walk Stop", 29.7200, -95.3530),
    "C": ("Elgin @ Main", 29.7400, -95.3500),
    "M": ("Middle @ Hamilton", 29.8000, -95.3500),
}


def at(year, month, day, hour, minute):
    """A tz-aware Houston datetime for the tests."""
    return datetime(year, month, day, hour, minute, tzinfo=HOUSTON)


def hms(text):
    """'16:12' or '24:05' as seconds after midnight, the way GTFS writes times."""
    hours, minutes = text.split(":")
    return int(hours) * 3600 + int(minutes) * 60


def weekday_services(day):
    """Mon-Fri run WKD, Saturday SAT, Sunday SUN."""
    if day.weekday() < 5:
        return {"WKD"}
    if day.weekday() == 5:
        return {"SAT"}
    return {"SUN"}


def make_trip(trip_id, route, stops, service="WKD", headsign="Downtown"):
    """stops: [(stop_id, arrival 'HH:MM', departure 'HH:MM', drop_off=True)] in travel order."""
    return {
        "trip_id": trip_id,
        "route": route,
        "headsign": headsign,
        "service_id": service,
        "stops": [
            (item[0], hms(item[1]), hms(item[2]), item[3] if len(item) > 3 else True) for item in stops
        ],
    }


class FakeSource(transit.GtfsSource):
    """An in-memory schedule that follows the GtfsSource interface."""

    def __init__(self, trips, services=weekday_services, stops=STOPS):
        self.trips = trips
        self.services = services
        self.stops = stops
        self.closed = 0

    def close(self):
        self.closed += 1

    def stops_near(self, lat, lng, miles):
        return [
            {"stop_id": sid, "name": name, "lat": slat, "lng": slng}
            for sid, (name, slat, slng) in self.stops.items()
            if haversine_miles(lat, lng, slat, slng) <= miles
        ]

    def service_ids(self, day):
        return set(self.services(day))

    def departures(self, stop_ids, after_sec, before_sec, service_ids):
        rows = []
        for trip in self.trips:
            if trip["service_id"] not in service_ids:
                continue
            for seq, (stop_id, _arr, dep, _drop) in enumerate(trip["stops"], 1):
                if stop_id in stop_ids and after_sec <= dep <= before_sec:
                    rows.append({
                        "trip_id": trip["trip_id"],
                        "route": trip["route"],
                        "headsign": trip["headsign"],
                        "stop_id": stop_id,
                        "stop_sequence": seq,
                        "dep_sec": dep,
                    })
        return rows

    def trip_stops_after(self, trip_id, stop_sequence):
        trip = next(t for t in self.trips if t["trip_id"] == trip_id)
        out = []
        for seq, (stop_id, arr, _dep, drop) in enumerate(trip["stops"], 1):
            if seq > stop_sequence:
                name, lat, lng = self.stops[stop_id]
                out.append({"stop_id": stop_id, "name": name, "stop_sequence": seq,
                            "arr_sec": arr, "lat": lat, "lng": lng, "drop_off": drop})
        return out


class BrokenSource(FakeSource):
    """Fails on every departure query, to check that directions() never raises."""

    def departures(self, stop_ids, after_sec, before_sec, service_ids):
        raise RuntimeError("boom")


def ask(now, source):
    """directions() from the origin to the destination."""
    return transit.directions(ORIGIN[0], ORIGIN[1], DEST[0], DEST[1], when=now, source=source)


@pytest.fixture(autouse=True)
def no_database(monkeypatch):
    """Every test starts without DATABASE_URL and with no database outage remembered."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(transit, "_down_until", 0.0)
    monkeypatch.setattr(transit, "_outage_reported", False)
    yield


# ---------------------------------------------------------------- walking

def test_walk_minutes_round_up_and_never_below_one():
    assert transit._walk_minutes(0) == 1
    assert transit._walk_minutes(0.01) == 1
    assert transit._walk_minutes(0.26) == 6  # 5.2 minutes rounds up to 6


def test_walking_minutes_come_from_the_distance():
    walk_w = math.ceil(haversine_miles(*ORIGIN, STOPS["W"][1], STOPS["W"][2]) / 3.0 * 60)
    source = FakeSource([
        make_trip("54-A", "54", [("A", "16:12", "16:12"), ("B", "16:31", "16:31")]),
        make_trip("W-B", "20", [("W", "16:10", "16:10"), ("B", "16:20", "16:20")]),
    ])
    d = ask(at(2026, 10, 12, 16, 0), source)
    assert d["legs"][0]["board_stop"] == "Walk Stop"
    assert d["walk_to_min"] == walk_w
    assert d["walk_from_min"] == 1  # B is about 0.006 mi from the destination
    assert d["arrive_by"] == "16:21"


# ---------------------------------------------------------------- direct rides

def test_direct_ride_picked_over_a_later_one():
    source = FakeSource([
        make_trip("54-1612", "54", [("A", "16:12", "16:12"), ("M", "16:20", "16:21"), ("B", "16:31", "16:31")]),
        make_trip("54-1630", "54", [("A", "16:30", "16:30"), ("M", "16:38", "16:39"), ("B", "16:49", "16:49")]),
    ])
    d = ask(at(2026, 10, 12, 16, 0), source)
    assert len(d["legs"]) == 1
    leg = d["legs"][0]
    assert leg["route"] == "54"
    assert leg["board_stop"] == "Scott @ Holman"
    assert leg["alight_stop"] == "Main @ Gray"
    assert leg["departs"] == "16:12"
    assert leg["arrives"] == "16:31"
    assert leg["stops"] == 2
    assert d["arrive_by"] == "16:32"  # 16:31 plus the 1-minute walk


def test_earliest_arrival_wins_even_when_it_leaves_later():
    source = FakeSource([
        make_trip("54-1612", "54", [("A", "16:12", "16:12"), ("B", "16:31", "16:31")]),
        make_trip("9X-1614", "9X", [("A", "16:14", "16:14"), ("B", "16:25", "16:25")]),
    ])
    d = ask(at(2026, 10, 12, 16, 0), source)
    assert d["legs"][0]["route"] == "9X"
    assert d["arrive_by"] == "16:26"


def test_cannot_catch_a_bus_that_leaves_before_the_walk_is_done():
    # Today's 16:00 bus is gone before the walk ends, so the first usable ride is tomorrow's 16:00 run.
    source = FakeSource([make_trip("54-1600", "54", [("A", "16:00", "16:00"), ("B", "16:12", "16:12")])])
    d = ask(at(2026, 10, 12, 16, 0), source)
    assert d["later"] is True
    assert d["legs"][0]["departs"] == "16:00"
    assert d["arrive_by"] == "16:13"


def test_no_ride_inside_the_wait_limit_gives_the_first_ride_later():
    source = FakeSource([make_trip("late", "54", [("A", "19:00", "19:00"), ("B", "19:12", "19:12")])])
    d = ask(at(2026, 10, 12, 16, 0), source)
    assert d["later"] is True
    assert d["legs"][0]["departs"] == "19:00"


def test_ride_within_the_wait_limit_is_not_marked_later():
    source = FakeSource([make_trip("54-1612", "54", [("A", "16:12", "16:12"), ("B", "16:31", "16:31")])])
    d = ask(at(2026, 10, 12, 16, 0), source)
    assert "later" not in d


def test_nothing_in_24_hours_gives_none_even_with_a_later_search():
    source = FakeSource([make_trip("54-AM", "54", [("A", "16:12", "16:12"), ("M", "16:20", "16:20")])])
    assert ask(at(2026, 10, 12, 16, 0), source) is None


def test_later_search_stops_at_24_hours():
    # Tuesday 16:30 is just outside the 24 hours from Monday 16:00 (the window ends Tuesday 16:01).
    source = FakeSource([make_trip("54-1630", "54", [("A", "16:30", "16:30"), ("B", "16:42", "16:42")])],
                        services=lambda day: {"WKD"} if day == MONDAY else set())
    assert ask(at(2026, 10, 12, 16, 0), source) is not None  # Monday 16:30 is inside 90 minutes
    source = FakeSource([make_trip("tue", "54", [("A", "16:30", "16:30"), ("B", "16:42", "16:42")], service="TUE")],
                        services=lambda day: {"TUE"} if day == date(2026, 10, 13) else set())
    assert ask(at(2026, 10, 12, 16, 0), source) is None


def test_later_ride_is_described_as_later_in_every_language():
    d = dict(one_leg_result(), later=True)
    for language in ["en", "es", "vi", "zh", "ar"]:
        for channel in ["sms", "kiosk"]:
            text = transit.summary(d, language, channel)
            assert text.startswith(transit._LATER[language]), (language, channel)
            assert transit.summary(one_leg_result(), language, channel) in text
    text = transit.summary(d, "en", "sms")
    assert text == "No buses are running now. The first one: Bus 54 from Scott@Holman 4:12PM, 6 stops to Main@Gray, walk 2 min."


def test_no_route_at_all_gives_none():
    source = FakeSource([make_trip("54-AM", "54", [("A", "16:12", "16:12"), ("M", "16:20", "16:20")])])
    assert ask(at(2026, 10, 12, 16, 0), source) is None


def test_no_stop_near_the_origin_gives_none():
    source = FakeSource([make_trip("54-1612", "54", [("A", "16:12", "16:12"), ("B", "16:31", "16:31")])])
    far = transit.directions(29.9, -95.9, DEST[0], DEST[1], when=at(2026, 10, 12, 16, 0), source=source)
    assert far is None


# ---------------------------------------------------------------- transfers

def test_one_transfer_when_there_is_no_direct_ride():
    source = FakeSource([
        make_trip("12-1605", "12", [("A", "16:05", "16:05"), ("C", "16:25", "16:25")]),
        make_trip("50-1630", "50", [("C", "16:30", "16:30"), ("B", "16:40", "16:40")]),
    ])
    d = ask(at(2026, 10, 12, 16, 0), source)
    assert len(d["legs"]) == 2
    first, second = d["legs"]
    assert (first["route"], first["departs"], first["arrives"]) == ("12", "16:05", "16:25")
    assert first["alight_stop"] == "Elgin @ Main"
    assert (second["route"], second["board_stop"]) == ("50", "Elgin @ Main")
    assert (second["departs"], second["arrives"]) == ("16:30", "16:40")
    assert d["arrive_by"] == "16:41"


def test_transfer_needs_two_minutes_to_make_the_connection():
    source = FakeSource([
        make_trip("12-1605", "12", [("A", "16:05", "16:05"), ("C", "16:25", "16:25")]),
        make_trip("50-1626", "50", [("C", "16:26", "16:26"), ("B", "16:40", "16:40")]),
    ])
    assert ask(at(2026, 10, 12, 16, 0), source) is None


# ---------------------------------------------------------------- times and services

def test_times_past_midnight_on_the_same_service_day():
    source = FakeSource([make_trip("night", "54", [("A", "24:05", "24:05"), ("B", "24:10", "24:10")])])
    d = ask(at(2026, 10, 12, 23, 50), source)
    assert d["legs"][0]["departs"] == "00:05"
    assert d["legs"][0]["arrives"] == "00:10"
    assert d["arrive_by"] == "00:11"


def test_times_past_midnight_use_yesterdays_service():
    monday_only = lambda day: {"MON"} if day == MONDAY else set()  # noqa: E731
    source = FakeSource(
        [make_trip("night2", "54", [("A", "24:20", "24:20"), ("B", "24:25", "24:25")], service="MON")],
        services=monday_only,
    )
    d = ask(at(2026, 10, 13, 0, 10), source)  # Tuesday morning, Monday's service still running
    assert d["legs"][0]["departs"] == "00:20"
    assert d["legs"][0]["arrives"] == "00:25"
    assert d["arrive_by"] == "00:26"


def test_service_not_running_today_is_skipped():
    source = FakeSource([make_trip("sun", "54", [("A", "16:12", "16:12"), ("B", "16:31", "16:31")], service="SUN")])
    assert ask(at(2026, 10, 12, 16, 0), source) is None  # Monday: no Sunday service
    d = ask(at(2026, 10, 11, 16, 0), source)  # Sunday: it runs
    assert d["legs"][0]["departs"] == "16:12"


# ---------------------------------------------------------------- sources

def test_source_error_gives_none_and_does_not_raise():
    source = BrokenSource([make_trip("54-1612", "54", [("A", "16:12", "16:12"), ("B", "16:31", "16:31")])])
    assert ask(at(2026, 10, 12, 16, 0), source) is None


def test_database_source_returns_empty_results_when_database_is_down():
    source = transit.DbSource(DEAD_URL)
    assert source.stops_near(ORIGIN[0], ORIGIN[1], 0.5) == []
    assert source.service_ids(MONDAY) == set()
    assert source.departures(["A"], 0, 86400, {"WKD"}) == []
    assert source.trip_stops_after("T1", 0) == []
    assert ask(at(2026, 10, 12, 16, 0), source) is None


def test_database_source_without_url_gives_empty_results():
    source = transit.DbSource()
    assert source.service_ids(MONDAY) == set()
    assert source.departures(["A"], 0, 86400, {"WKD"}) == []


def test_local_source_reads_a_small_hand_written_file(tmp_path):
    path = tmp_path / "transit_local.json"
    path.write_text(json.dumps({
        "stops": [
            {"stop_id": "A", "name": "Scott @ Holman", "lat": 29.7201, "lng": -95.35},
            {"stop_id": "B", "name": "Main @ Gray", "lat": 29.7300, "lng": -95.3501},
        ],
        "trips": [{
            "trip_id": "T1", "route": "54", "headsign": "Downtown", "service_id": "WKD",
            "stops": [
                {"stop_id": "A", "name": "Scott @ Holman", "stop_sequence": 1, "arrival": hms("16:12"),
                 "departure": hms("16:12"), "lat": 29.7201, "lng": -95.35, "pickup_type": 0, "drop_off_type": 0},
                {"stop_id": "B", "name": "Main @ Gray", "stop_sequence": 2, "arrival": hms("16:31"),
                 "departure": hms("16:31"), "lat": 29.7300, "lng": -95.3501, "pickup_type": 0, "drop_off_type": 0},
            ],
        }],
        "calendar": [{"service_id": "WKD", "days": [True] * 5 + [False] * 2,
                      "start_date": "2026-01-01", "end_date": "2026-12-31"}],
        "calendar_dates": [{"service_id": "WKD", "date": "2026-10-19", "exception_type": 2}],
    }), encoding="utf-8")
    source = transit.LocalSource(path)

    assert [s["stop_id"] for s in source.stops_near(*ORIGIN, 0.5)] == ["A"]
    assert source.service_ids(MONDAY) == {"WKD"}
    assert source.service_ids(date(2026, 10, 19)) == set()  # holiday removed by calendar_dates
    assert source.service_ids(date(2026, 10, 10)) == set()  # Saturday: calendar says weekdays only
    rows = source.departures(["A"], hms("16:00"), hms("17:00"), {"WKD"})
    assert [(r["route"], r["dep_sec"], r["stop_sequence"]) for r in rows] == [("54", hms("16:12"), 1)]
    after = source.trip_stops_after("T1", 1)
    assert [(s["stop_id"], s["arr_sec"]) for s in after] == [("B", hms("16:31"))]


GTFS_FILES = {
    "metro-gtfs/stops.txt": (
        "stop_id,stop_name,stop_lat,stop_lon\n"
        "A,Scott @ Holman,29.7201,-95.3500\n"
        "B,Main @ Gray,29.7300,-95.3501\n"
        "W,Walk Stop,29.7200,-95.3530\n"
        "M,Middle @ Hamilton,29.8000,-95.3500\n"
    ),
    "routes.txt": (
        "route_id,agency_id,route_short_name,route_long_name,route_type\n"
        "R54,1,54,Scott,3\n"
    ),
    "trips.txt": (
        "route_id,service_id,trip_id,trip_headsign\n"
        "R54,WKD,T54,Downtown\n"
        "R54,SUN1,TSUN,Downtown\n"
    ),
    "stop_times.txt": (
        "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
        "T54,16:12:00,16:12:00,A,1\n"
        "T54,,,M,2\n"  # no times at all: filled in from A and B (16:21:30)
        "T54,16:31:00,,B,3\n"  # empty departure: copied from the arrival
        "TSUN,16:12:00,16:12:00,A,1\n"
        "TSUN,16:31:00,16:31:00,B,2\n"
    ),
    "calendar.txt": (
        "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
        "WKD,1,1,1,1,1,0,0,20260101,20261231\n"
        "SUN1,0,0,0,0,0,0,1,20260101,20261231\n"
    ),
    "calendar_dates.txt": (
        "service_id,date,exception_type\n"
        "WKD,20261019,2\n"  # holiday: weekday service removed
        "WKD,20261011,1\n"  # a Sunday: weekday service added
    ),
}


def make_gtfs_zip(folder):
    """A small GTFS zip (files in a subfolder, as some feeds ship them) in the given folder."""
    path = folder / "metro-gtfs.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in GTFS_FILES.items():
            archive.writestr(name, text)
    return path


def test_loader_writes_local_file_that_directions_can_use(tmp_path, monkeypatch):
    zip_path = make_gtfs_zip(tmp_path)
    out_path = tmp_path / "transit_local.json"
    with zipfile.ZipFile(zip_path) as archive:
        load_gtfs.write_local(load_gtfs.Feed(archive), ORIGIN[0], ORIGIN[1], out_path=out_path)

    monkeypatch.setattr(transit, "LOCAL_PATH", out_path)  # no DATABASE_URL, so the local file is used
    d = transit.directions(ORIGIN[0], ORIGIN[1], DEST[0], DEST[1], when=at(2026, 10, 12, 16, 0))
    assert d["legs"][0]["route"] == "54"
    assert d["legs"][0]["board_stop"] == "Scott @ Holman"
    assert d["legs"][0]["alight_stop"] == "Main @ Gray"
    assert d["arrive_by"] == "16:32"


def test_loader_local_file_applies_calendar_dates(tmp_path):
    zip_path = make_gtfs_zip(tmp_path)
    out_path = tmp_path / "transit_local.json"
    with zipfile.ZipFile(zip_path) as archive:
        load_gtfs.write_local(load_gtfs.Feed(archive), ORIGIN[0], ORIGIN[1], out_path=out_path)
    source = transit.LocalSource(out_path)
    assert "WKD" in source.service_ids(MONDAY)
    assert "WKD" not in source.service_ids(date(2026, 10, 19))  # removed holiday
    assert "WKD" in source.service_ids(date(2026, 10, 11))  # added on a Sunday


def test_load_gtfs_seconds_past_midnight():
    assert load_gtfs._seconds("25:10:00") == 90600
    assert load_gtfs._seconds("") is None


# ---------------------------------------------------------------- summaries

KIOSK_CLOCK = {"en": "4:12 PM", "es": "4:12 p. m.", "vi": "4:12 chiều", "zh": "下午4:12", "ar": "4:12 م"}
SMS_CLOCK = {"en": "4:12PM", "es": "4:12 p. m.", "vi": "4:12 chiều", "zh": "下午4:12", "ar": "4:12 م"}


def one_leg_result():
    return {
        "legs": [{"route": "54", "headsign": "Downtown", "board_stop": "Scott @ Holman",
                  "alight_stop": "Main @ Gray", "departs": "16:12", "arrives": "16:31", "stops": 6}],
        "walk_to_min": 3,
        "walk_from_min": 2,
        "arrive_by": "16:33",
    }


def test_summary_kiosk_english_says_at_not_the_at_sign():
    text = transit.summary(one_leg_result(), "en", "kiosk")
    assert "Scott at Holman" in text
    assert "Main at Gray" in text
    assert "4:12 PM" in text
    assert "@" not in text
    assert "walk 2 minutes" in text


def test_summary_sms_english_keeps_the_at_sign():
    text = transit.summary(one_leg_result(), "en", "sms")
    assert text == "Bus 54 from Scott@Holman 4:12PM, 6 stops to Main@Gray, walk 2 min."


@pytest.mark.parametrize("language", ["en", "es", "vi", "zh", "ar"])
def test_summary_kiosk_all_languages(language):
    text = transit.summary(one_leg_result(), language, "kiosk")
    assert "54" in text
    assert KIOSK_CLOCK[language] in text
    assert "@" not in text


@pytest.mark.parametrize("language", ["en", "es", "vi", "zh", "ar"])
def test_summary_sms_all_languages(language):
    text = transit.summary(one_leg_result(), language, "sms")
    assert "Scott@Holman" in text
    assert "Main@Gray" in text
    assert "54" in text
    assert SMS_CLOCK[language] in text
    assert len(text) < 300


def test_summary_two_legs_describes_the_transfer():
    d = {
        "legs": [
            {"route": "12", "headsign": "Uptown", "board_stop": "Scott @ Holman", "alight_stop": "Elgin @ Main",
             "departs": "16:05", "arrives": "16:25", "stops": 1},
            {"route": "50", "headsign": "Downtown", "board_stop": "Elgin @ Main", "alight_stop": "Main @ Gray",
             "departs": "16:30", "arrives": "16:40", "stops": 1},
        ],
        "walk_to_min": 1, "walk_from_min": 1, "arrive_by": "16:41",
    }
    kiosk = transit.summary(d, "en", "kiosk")
    assert "Transfer to bus 50" in kiosk
    assert "4:30 PM" in kiosk and "4:12 PM" not in kiosk
    sms = transit.summary(d, "en", "sms")
    assert sms.startswith("Bus 12 from Scott@Holman 4:05PM, 1 stop to Elgin@Main, bus 50 from Elgin@Main")


def test_summary_without_a_ride_and_unknown_language_falls_back():
    assert transit.summary(None, "en", "sms").startswith("No direct bus")
    assert transit.summary(one_leg_result(), "fr", "kiosk").startswith("Walk 3 minutes")
    assert transit.summary(None, "ar", "kiosk")


def two_leg_result():
    """A transfer with short names, for length and wording checks."""
    return {
        "legs": [
            {"route": "12", "headsign": "Uptown", "board_stop": "Scott @ Holman", "alight_stop": "Elgin @ Main",
             "departs": "16:05", "arrives": "16:25", "stops": 1},
            {"route": "50", "headsign": "Downtown", "board_stop": "Elgin @ Main", "alight_stop": "Main @ Gray",
             "departs": "16:30", "arrives": "16:40", "stops": 1},
        ],
        "walk_to_min": 1, "walk_from_min": 1, "arrive_by": "16:41",
    }


# ---------------------------------------------------------------- item 2: connections, batching, caps


def test_directions_closes_the_source_when_done():
    source = FakeSource([make_trip("54-1612", "54", [("A", "16:12", "16:12"), ("B", "16:31", "16:31")])])
    ask(at(2026, 10, 12, 16, 0), source)
    assert source.closed == 1
    ask(at(2026, 10, 12, 16, 0), BrokenSource([]))  # still closes even when the search fails
    source2 = FakeSource([])
    ask(at(2026, 10, 12, 16, 0), source2)
    assert source2.closed == 1


def test_departures_one_per_trip_and_capped():
    trips = [make_trip(f"T{i:02d}", "54", [("A", f"16:{i:02d}", f"16:{i:02d}"), ("B", f"16:{i + 10:02d}", f"16:{i + 10:02d}")])
             for i in range(60)]
    search = transit._Search(FakeSource(trips))
    deps = search.departures(["A"], at(2026, 10, 12, 16, 0), at(2026, 10, 12, 17, 0))
    assert len(deps) == transit.MAX_DEPARTURES == 40
    assert deps[0]["dep_dt"] == at(2026, 10, 12, 16, 0)

    dup = FakeSource([make_trip("dup", "54", [("A", "16:05", "16:05"), ("W", "16:07", "16:07")])])
    rows = transit._Search(dup).departures(["A", "W"], at(2026, 10, 12, 16, 0), at(2026, 10, 12, 17, 0))
    assert len(rows) == 1  # the same trip boarding at two stops counts once


def test_batched_trip_loading_is_used_by_the_search():
    class CountingSource(FakeSource):
        batches = 0

        def trip_stops_after_many(self, trip_ids):
            CountingSource.batches += 1
            return {tid: FakeSource.trip_stops_after(self, tid, 0) for tid in trip_ids}

    source = CountingSource([make_trip("54-1612", "54", [("A", "16:12", "16:12"), ("B", "16:31", "16:31")])])
    d = ask(at(2026, 10, 12, 16, 0), source)
    assert d["legs"][0]["route"] == "54"
    assert CountingSource.batches >= 1


# ---------------------------------------------------------------- item 3: stop counts with no-drop stops

def test_stop_count_includes_stops_where_riders_cannot_get_off():
    source = FakeSource([make_trip("54-skip", "54", [
        ("A", "16:12", "16:12"),
        ("M", "16:20", "16:20", False),  # riders can board here but not get off
        ("B", "16:31", "16:31"),
    ])])
    d = ask(at(2026, 10, 12, 16, 0), source)
    assert d["legs"][0]["stops"] == 2
    assert d["legs"][0]["alight_stop"] == "Main @ Gray"


def test_cannot_alight_where_drop_off_is_not_allowed():
    source = FakeSource([make_trip("54-nodrop", "54", [
        ("A", "16:12", "16:12"),
        ("B", "16:31", "16:31", False),  # next to the destination, but no getting off here
        ("M", "16:40", "16:40"),
    ])])
    assert ask(at(2026, 10, 12, 16, 0), source) is None


# ---------------------------------------------------------------- item 4: plurals

def test_stop_and_minute_plurals_by_language():
    assert transit._stops_phrase("en", 1) == "1 stop"
    assert transit._stops_phrase("en", 6) == "6 stops"
    assert transit._stops_phrase("es", 1) == "1 parada"
    assert transit._stops_phrase("es", 6) == "6 paradas"
    assert transit._stops_phrase("vi", 3) == "3 trạm"
    assert transit._stops_phrase("zh", 3) == "3站"
    assert transit._stops_phrase("ar", 1) == "محطة واحدة"
    assert transit._stops_phrase("ar", 2) == "محطتان"
    assert transit._stops_phrase("ar", 5) == "5 محطات"
    assert transit._stops_phrase("ar", 12) == "12 محطة"
    assert transit._minutes_phrase("en", 1) == "1 minute"
    assert transit._minutes_phrase("ar", 1) == "دقيقة"
    assert transit._minutes_phrase("ar", 2) == "دقيقتان"
    assert transit._minutes_phrase("ar", 5) == "5 دقائق"
    assert transit._minutes_phrase("ar", 12) == "12 دقيقة"


def test_kiosk_uses_singular_for_one_stop_and_one_minute():
    d = one_leg_result()
    d["legs"][0]["stops"] = 1
    d["walk_to_min"] = 1
    text = transit.summary(d, "en", "kiosk")
    assert "after 1 stop," in text and "Walk 1 minute to" in text


# ---------------------------------------------------------------- item 6: wording and layout

def test_sms_starts_with_the_bus_and_stays_short_for_two_legs():
    for language in ["en", "es", "vi", "zh", "ar"]:
        text = transit.summary(two_leg_result(), language, "sms")
        assert len(text) <= 160, (language, len(text), text)
    en = transit.summary(two_leg_result(), "en", "sms")
    assert en.startswith("Bus 12 ")
    assert en.count("Walk") == 0  # the walk is lowercase mid-sentence
    assert transit.summary(one_leg_result(), "en", "sms") == "Bus 54 from Scott@Holman 4:12PM, 6 stops to Main@Gray, walk 2 min."


def test_spanish_says_a_la_for_one_oclock():
    assert transit._time_at("13:05", "es") == "a la 1:05 p. m."
    assert transit._time_at("16:12", "es") == "a las 4:12 p. m."


def test_vietnamese_evening_is_toi_from_six():
    assert transit._clock("18:30", "vi", False) == "6:30 tối"
    assert transit._clock("16:30", "vi", False) == "4:30 chiều"


def test_chinese_intersection_name_and_no_stray_spaces():
    text = transit.summary(one_leg_result(), "zh", "kiosk")
    assert "Scott与Holman交叉口" in text
    assert " " not in text
    assert "@" not in text
    assert transit.summary(one_leg_result(), "zh", "sms").count("Scott@Holman") == 1


# ---------------------------------------------------------------- item 7: missing stop times

def test_missing_stop_times_are_interpolated_or_copied():
    rows = [
        ("T", 36000, 36000, "A", 1, 0, 0),
        ("T", None, None, "B", 2, 0, 0),
        ("T", None, None, "C", 3, 0, 0),
        ("T", 37200, 37200, "D", 4, 0, 0),
        ("T", None, None, "E", 5, 0, 0),  # after the last timed stop: copies it
    ]
    filled = load_gtfs._fill_trip(rows)
    assert [r[1] for r in filled] == [36000, 36400, 36800, 37200, 37200]
    assert [r[2] for r in filled] == [36000, 36400, 36800, 37200, 37200]


def test_trip_with_no_times_at_all_is_dropped():
    assert load_gtfs._fill_trip([("T", None, None, "A", 1, 0, 0)]) == []


def test_loader_fills_a_stop_with_no_times(tmp_path):
    zip_path = make_gtfs_zip(tmp_path)
    out_path = tmp_path / "transit_local.json"
    with zipfile.ZipFile(zip_path) as archive:
        load_gtfs.write_local(load_gtfs.Feed(archive), ORIGIN[0], ORIGIN[1], out_path=out_path)
    data = json.loads(out_path.read_text(encoding="utf-8"))
    trip = next(t for t in data["trips"] if t["trip_id"] == "T54")
    middle = next(s for s in trip["stops"] if s["stop_id"] == "M")
    assert middle["arrival"] == 58890  # 16:21:30
    assert middle["departure"] == 58890


# ---------------------------------------------------------------- item 8: service-day origin (DST)

def test_service_origin_is_noon_minus_twelve_hours():
    assert transit._service_origin(MONDAY) == datetime(2026, 10, 12, 5, 0, tzinfo=timezone.utc)  # midnight CDT
    # Clocks go back on 2026-11-01: noon CST minus 12 hours is 01:00 CDT, not midnight.
    assert transit._service_origin(date(2026, 11, 1)) == datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc)



# ---------------------------------------------------------------- nearest stops when no ride is found

def test_closest_stops_name_the_nearest_stop_to_each_point():
    source = FakeSource([])
    start, end = transit.closest_stops(ORIGIN[0], ORIGIN[1], DEST[0], DEST[1], "en", "kiosk", source=source)
    assert start == "Scott at Holman"  # A is next to the origin
    assert end == "Main at Gray"  # B is next to the destination
    assert source.closed == 1


def test_closest_stops_sms_keeps_the_at_sign_and_other_languages_format():
    source = FakeSource([])
    assert transit.closest_stops(ORIGIN[0], ORIGIN[1], DEST[0], DEST[1], "en", "sms", source=source) == (
        "Scott@Holman", "Main@Gray")
    zh = transit.closest_stops(ORIGIN[0], ORIGIN[1], DEST[0], DEST[1], "zh", "kiosk", source=FakeSource([]))
    assert zh == ("Scott与Holman交叉口", "Main与Gray交叉口")


def test_closest_stops_none_without_a_schedule_or_nearby_stop(monkeypatch, tmp_path):
    monkeypatch.setattr(transit, "LOCAL_PATH", tmp_path / "no_such_file.json")  # no local schedule either
    assert transit.closest_stops(ORIGIN[0], ORIGIN[1], DEST[0], DEST[1], "en", "sms", source=None) is None
    assert transit.closest_stops(29.9, -95.9, DEST[0], DEST[1], "en", "sms", source=FakeSource([])) is None


def test_closest_stops_never_raises_on_a_broken_source():
    class NoStops(FakeSource):
        def stops_near(self, lat, lng, miles):
            raise RuntimeError("boom")

    assert transit.closest_stops(ORIGIN[0], ORIGIN[1], DEST[0], DEST[1], "en", "sms",
                                 source=NoStops([])) is None


# ---------------------------------------------------------------- 24-hour search pages past busy stops

def test_later_search_pages_past_trips_that_do_not_reach_the_destination():
    # 45 route-99 trips leave A from 16:00 Monday and never reach B, so the first 40 fill the cap.
    # The route-54 trip to B is Tuesday 08:00: only a later batch can find it.
    trips = [make_trip(f"99-{i}", "99", [("A", f"16:{i:02d}", f"16:{i:02d}"), ("M", f"16:{i + 5:02d}", f"16:{i + 5:02d}")])
             for i in range(45)]
    trips.append(make_trip("54-tue", "54", [("A", "08:00", "08:00"), ("B", "08:12", "08:12")], service="TUE"))
    source = FakeSource(trips, services=lambda day: {"TUE"} if day == date(2026, 10, 13) else weekday_services(day))
    d = ask(at(2026, 10, 12, 16, 0), source)
    assert d["later"] is True
    assert d["legs"][0]["route"] == "54"
    assert d["legs"][0]["departs"] == "08:00"
    assert d["buses_running"] is True  # the 99 buses run, they just do not go there


def test_later_search_gives_up_after_its_batch_limit(monkeypatch):
    monkeypatch.setattr(transit, "LATER_BATCHES", 1)
    trips = [make_trip(f"99-{i}", "99", [("A", f"16:{i:02d}", f"16:{i:02d}"), ("M", f"16:{i + 5:02d}", f"16:{i + 5:02d}")])
             for i in range(45)]
    trips.append(make_trip("54-tue", "54", [("A", "08:00", "08:00"), ("B", "08:12", "08:12")], service="TUE"))
    source = FakeSource(trips, services=lambda day: {"TUE"} if day == date(2026, 10, 13) else weekday_services(day))
    assert ask(at(2026, 10, 12, 16, 0), source) is None


def test_wording_no_buses_running_when_nothing_leaves_the_stops_in_90_minutes():
    source = FakeSource([make_trip("54-tue", "54", [("A", "08:00", "08:00"), ("B", "08:12", "08:12")], service="TUE")],
                        services=lambda day: {"TUE"} if day == date(2026, 10, 13) else set())
    d = ask(at(2026, 10, 12, 16, 0), source)
    assert d["buses_running"] is False
    assert transit.summary(d, "en", "kiosk").startswith("No buses are running now. The first one:")
    assert transit.summary(d, "en", "sms").startswith("No buses are running now. The first one: Bus 54")


def test_wording_next_bus_when_buses_run_but_none_goes_there():
    trips = [make_trip("99-1610", "99", [("A", "16:10", "16:10"), ("M", "16:20", "16:20")])]
    trips.append(make_trip("54-tue", "54", [("A", "08:00", "08:00"), ("B", "08:12", "08:12")], service="TUE"))
    source = FakeSource(trips, services=lambda day: {"TUE"} if day == date(2026, 10, 13) else weekday_services(day))
    d = ask(at(2026, 10, 12, 16, 0), source)
    assert d["later"] is True and d["buses_running"] is True
    assert transit.summary(d, "en", "sms").startswith("The next bus that goes there: Bus 54")
    for language in ["es", "vi", "zh", "ar"]:
        assert transit.summary(d, language, "kiosk").startswith(transit._NEXT_BUS[language])
        assert transit._NEXT_BUS[language] != transit._LATER[language]


def test_search_gives_up_when_over_its_time_budget(monkeypatch):
    # A slow database must not make the kiosk time out: past the budget, no ride is returned.
    monkeypatch.setattr(transit, "TIME_BUDGET_SEC", -1)
    source = FakeSource([make_trip("54-tue", "54", [("A", "08:00", "08:00"), ("B", "08:12", "08:12")], service="TUE")],
                        services=lambda day: {"TUE"} if day == date(2026, 10, 13) else set())
    assert ask(at(2026, 10, 12, 16, 0), source) is None


def test_service_days_are_looked_up_once_per_search():
    calls = []
    source = FakeSource([make_trip("54-tue", "54", [("A", "08:00", "08:00"), ("B", "08:12", "08:12")], service="TUE")],
                        services=lambda day: calls.append(day) or ({"TUE"} if day == date(2026, 10, 13) else set()))
    assert ask(at(2026, 10, 12, 16, 0), source) is not None
    assert len(calls) == len(set(calls))
