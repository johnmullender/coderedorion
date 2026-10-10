"""Tests for matching.py. Uses a small in-test resource list, not the sample JSON file."""

import pathlib
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import matching  # noqa: E402

HOUSTON = ZoneInfo("America/Chicago")
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
ALL_DAY = {day: ["00:00-24:00"] for day in DAYS}  # open every day, all day
ORIGIN_LAT, ORIGIN_LNG = 29.72, -95.35  # the point we search from

# 2026-10-10 is a Saturday, 2026-10-12 is a Monday, 2026-10-13 is a Tuesday.


def at(year, month, day, hour, minute):
    """A tz-aware Houston datetime for the tests."""
    return datetime(year, month, day, hour, minute, tzinfo=HOUSTON)


def make_place(place_id, lat, lng, need="food", hours=None):
    """Build a small test resource. Missing days default to closed."""
    full_hours = {day: [] for day in DAYS}
    full_hours.update(hours or {})
    return {
        "id": place_id,
        "name": place_id,
        "category": [need],
        "lat": lat,
        "lng": lng,
        "hours": full_hours,
    }


def test_haversine_zero_distance():
    assert matching.haversine_miles(29.72, -95.35, 29.72, -95.35) == 0


def test_open_inside_range_and_closed_outside():
    place = make_place("p", 29.72, -95.35, hours={"mon": ["09:00-12:00"]})
    assert matching.is_open(place, at(2026, 10, 12, 10, 0)) is True
    assert matching.is_open(place, at(2026, 10, 12, 8, 59)) is False
    assert matching.is_open(place, at(2026, 10, 12, 12, 0)) is False  # end time is closed


def test_multiple_ranges_in_one_day():
    place = make_place("p", 29.72, -95.35, hours={"mon": ["09:00-12:00", "13:00-17:00"]})
    assert matching.is_open(place, at(2026, 10, 12, 12, 30)) is False
    assert matching.is_open(place, at(2026, 10, 12, 13, 30)) is True


def test_midnight_crossing_counts_for_previous_day():
    place = make_place("p", 29.72, -95.35, hours={"mon": ["19:00-07:00"]})
    assert matching.is_open(place, at(2026, 10, 12, 20, 0)) is True  # Monday evening
    assert matching.is_open(place, at(2026, 10, 13, 5, 0)) is True  # Tuesday 5 AM, Monday's entry
    assert matching.is_open(place, at(2026, 10, 13, 8, 0)) is False  # Tuesday 8 AM, past the end
    assert matching.is_open(place, at(2026, 10, 12, 6, 0)) is False  # Monday 6 AM, Sunday is empty


def test_midnight_crossing_closes_at_next_morning():
    place = make_place("p", 29.72, -95.35, hours={"mon": ["19:00-07:00"]})
    results = matching.find_matches("food", ORIGIN_LAT, ORIGIN_LNG,
                                    when=at(2026, 10, 13, 5, 0), resources=[place])
    assert results["options"][0]["closes_at"] == "07:00"


def test_all_day_range_has_no_closing_time():
    place = make_place("p", 29.72, -95.35, hours={"sat": ["00:00-24:00"]})
    assert matching.is_open(place, at(2026, 10, 10, 23, 59)) is True
    results = matching.find_matches("food", ORIGIN_LAT, ORIGIN_LNG,
                                    when=at(2026, 10, 10, 23, 59), resources=[place])
    assert results["options"][0]["closes_at"] is None


def test_closes_at_is_end_of_current_range():
    place = make_place("p", 29.72, -95.35, hours={"mon": ["09:00-12:00", "13:00-17:00"]})
    results = matching.find_matches("food", ORIGIN_LAT, ORIGIN_LNG,
                                    when=at(2026, 10, 12, 10, 0), resources=[place])
    match = results["options"][0]
    assert match["closes_at"] == "12:00"
    assert match["open_now"] is True


def test_nearest_first_and_limit():
    when = at(2026, 10, 10, 10, 0)  # Saturday morning, every place is open
    far = make_place("far", 29.75, -95.35, hours=ALL_DAY)
    near = make_place("near", 29.7210, -95.3510, hours=ALL_DAY)
    mid = make_place("mid", 29.725, -95.355, hours=ALL_DAY)
    resources = [far, mid, near]

    everything = matching.find_matches("food", ORIGIN_LAT, ORIGIN_LNG, when=when, resources=resources)
    assert [m["id"] for m in everything["options"]] == ["near", "mid", "far"]
    distances = [m["distance_mi"] for m in everything["options"]]
    assert distances == sorted(distances)

    two = matching.find_matches("food", ORIGIN_LAT, ORIGIN_LNG, when=when, resources=resources, limit=2)
    assert [m["id"] for m in two["options"]] == ["near", "mid"]


def test_only_open_places_and_matching_need_are_returned():
    when = at(2026, 10, 10, 10, 0)  # Saturday morning
    closed_nearby = make_place("closed", 29.7210, -95.3510, hours={"mon": ["09:00-12:00"]})
    wrong_need = make_place("shelter", 29.7210, -95.3510, need="shelter", hours=ALL_DAY)
    open_far = make_place("open", 29.75, -95.35, hours=ALL_DAY)
    results = matching.find_matches("food", ORIGIN_LAT, ORIGIN_LNG, when=when,
                                    resources=[closed_nearby, wrong_need, open_far])
    assert [m["id"] for m in results["options"]] == ["open"]


def test_next_open_when_all_closed():
    place = make_place("monday-place", 29.72, -95.35, hours={"mon": ["09:00-12:00"]})
    results = matching.find_matches("food", ORIGIN_LAT, ORIGIN_LNG,
                                    when=at(2026, 10, 10, 10, 0), resources=[place])
    assert results["options"] == []
    next_open = results["next_open"]
    assert next_open["id"] == "monday-place"
    assert next_open["opens_at"] == "Mon 09:00"
    assert next_open["open_now"] is False


def test_next_open_picks_soonest_place():
    # Saturday 10 AM: Sunday 6 PM comes before Monday 9 AM.
    monday = make_place("monday", 29.7210, -95.3510, hours={"mon": ["09:00-12:00"]})
    sunday = make_place("sunday", 29.75, -95.35, hours={"sun": ["18:00-20:00"]})
    results = matching.find_matches("food", ORIGIN_LAT, ORIGIN_LNG,
                                    when=at(2026, 10, 10, 10, 0), resources=[monday, sunday])
    assert results["next_open"]["id"] == "sunday"
    assert results["next_open"]["opens_at"] == "Sun 18:00"


def test_next_open_none_when_nothing_opens_within_7_days():
    place = make_place("never", 29.72, -95.35, hours={})
    results = matching.find_matches("food", ORIGIN_LAT, ORIGIN_LNG,
                                    when=at(2026, 10, 10, 10, 0), resources=[place])
    assert results["options"] == []
    assert results["next_open"] is None


def test_resources_without_location_are_ignored():
    no_location = make_place("nowhere", None, None, hours=ALL_DAY)
    results = matching.find_matches("food", ORIGIN_LAT, ORIGIN_LNG,
                                    when=at(2026, 10, 10, 10, 0), resources=[no_location])
    assert results["options"] == []
    assert results["next_open"] is None


def test_load_resources_reads_sample_file():
    rows = matching.load_resources()
    required = {"id", "name", "category", "address", "lat", "lng", "hours", "requirements",
                "languages", "phone", "notes", "source_url", "last_verified"}
    assert len(rows) == 12
    needs = set()
    for row in rows:
        assert required <= set(row.keys()), row.get("id")
        assert row["name"].startswith("Sample ")
        assert row["id"].startswith("sample-")
        assert set(row["hours"].keys()) == set(DAYS)
        needs.update(row["category"])
    assert needs == {"food", "shelter", "hygiene", "clinic", "library", "cooling", "legal"}
