"""Tests for scripts/validate.py and scripts/geocode.py. No network: downloads and lookups are faked."""

import json
import pathlib
import sys

import httpx
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import geocode  # noqa: E402
import validate  # noqa: E402

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def good_place(**overrides):
    """A place that passes every check. Override fields per test."""
    hours = {day: [] for day in DAYS}
    hours["mon"] = ["09:00-17:00"]
    place = {
        "id": "test-pantry",
        "name": "Test Pantry",
        "category": ["food"],
        "address": "1 Main St, Houston, TX 77002",
        "lat": 29.7604,
        "lng": -95.3698,
        "coords_source": "census",
        "hours": hours,
        "hours_quote": "Mon 9 AM - 5 PM",
        "eligibility": [{"type": "id_required", "source_quote": "Please bring a photo ID."}],
        "services": "Food",
        "languages": ["en"],
        "phone": "713-555-0100",
        "notes": "",
        "source_url": "https://example.org/pantry",
        "last_verified": None,
        "needs_review": False,
    }
    place.update(overrides)
    return place


def with_hours(**days):
    """Good hours with the given day lists."""
    hours = {day: [] for day in DAYS}
    hours.update(days)
    return hours


def rule_errors(rule):
    """Errors for a place whose only eligibility rule is `rule`."""
    errors, _warnings = validate.validate_place(good_place(eligibility=[rule]))
    return errors


# ---------------------------------------------------------------- validate: schema

def test_valid_place_has_no_errors_or_warnings():
    assert validate.validate_place(good_place()) == ([], [])


def test_needs_review_true_is_a_warning_not_an_error():
    errors, warnings = validate.validate_place(good_place(needs_review=True))
    assert errors == []
    assert any("needs_review" in w for w in warnings)


def test_all_empty_hours_is_a_warning_not_an_error():
    errors, warnings = validate.validate_place(good_place(hours=with_hours()))
    assert errors == []
    assert any("hours are empty" in w for w in warnings)


def test_hours_missing_a_day_key_is_an_error():
    hours = with_hours(mon=["09:00-17:00"])
    del hours["sun"]
    errors, _ = validate.validate_place(good_place(hours=hours))
    assert any("sun" in e for e in errors)


@pytest.mark.parametrize("bad", ["9:00-12:00", "10:00", "25:00-26:00", "10:00-10:00",
                                 "10:00-24:30", "10:60-11:00", "10:00 - 12:00"])
def test_bad_hours_ranges_are_errors(bad):
    errors, _ = validate.validate_place(good_place(hours=with_hours(tue=[bad])))
    assert any("hours.tue" in e for e in errors), errors


def test_midnight_crossing_and_all_day_ranges_are_valid():
    hours = with_hours(fri=["18:00-07:00"], sat=["00:00-24:00"])
    assert validate.validate_place(good_place(hours=hours)) == ([], [])


@pytest.mark.parametrize("overrides", [{"lat": 40.0}, {"lng": -90.0}, {"lat": None},
                                       {"lng": "-95.3"}, {"lat": True}])
def test_out_of_bounds_or_bad_coordinates_are_errors(overrides):
    errors, _ = validate.validate_place(good_place(**overrides))
    assert any(e.startswith(("lat", "lng")) for e in errors), errors


def test_category_must_use_known_needs():
    errors, _ = validate.validate_place(good_place(category=["housing"]))
    assert any("unknown need" in e for e in errors)
    errors, _ = validate.validate_place(good_place(category=[]))
    assert any("category" in e for e in errors)


@pytest.mark.parametrize("url", ["", "example.org/page", "ftp://example.org/a", "https://"])
def test_source_url_must_be_http_or_https(url):
    errors, _ = validate.validate_place(good_place(source_url=url))
    assert any("source_url" in e for e in errors)


@pytest.mark.parametrize("rule", [
    {"type": "id_required"},
    {"type": "id_required", "source_quote": "   "},
])
def test_rule_without_source_quote_is_an_error(rule):
    assert any("no source_quote" in e for e in rule_errors(rule))


def test_unknown_rule_type_is_an_error():
    errors = rule_errors({"type": "veteran_only", "source_quote": "Veterans only."})
    assert any("unknown type" in e for e in errors)


@pytest.mark.parametrize("rule, expected", [
    ({"type": "age", "min": None, "max": None, "source_quote": "q"}, "both null"),
    ({"type": "age", "min": 30, "max": 18, "source_quote": "q"}, "greater than max"),
    ({"type": "serves", "group": "kids", "source_quote": "q"}, "group must be"),
    ({"type": "zip_in", "zips": ["7700"], "source_quote": "q"}, "5-digit"),
    ({"type": "note", "source_quote": "q"}, "text is missing"),
    ({"type": "residency", "source_quote": "q"}, "area is missing"),
])
def test_type_specific_fields_are_checked(rule, expected):
    errors = rule_errors(rule)
    assert any(expected in e for e in errors), errors


def test_duplicate_ids_are_reported_on_the_second_place():
    results = validate.validate_resources([good_place(), good_place(name="Other")])
    assert results[0][1] == []
    assert any("duplicate id" in e for e in results[1][1])


# ---------------------------------------------------------------- validate: quotes

def test_normalize_text_handles_case_quotes_dashes_and_spaces():
    assert validate.normalize_text("“Photo  ID” – 10 AM -\n6 PM") == '"photo id"-10 am-6 pm'


def test_quote_matches_despite_whitespace_and_curly_quotes():
    page = "<p>Bring a “photo ID”\n   to each visit — walk-ins welcome.</p>"
    text = validate.html_to_text(page)
    assert validate.quote_in_page('Bring a "photo ID" to each visit - walk-ins welcome.', text)


def test_missing_quote_is_not_found():
    assert not validate.quote_in_page("Bring a bag.", "Please bring a photo ID.")
    assert not validate.quote_in_page("   ", "anything at all")


def test_html_to_text_drops_scripts_styles_and_decodes_entities():
    page = ("<html><head><style>p {color: red}</style></head><body>"
            "<script>var x = 'Photo ID';</script><!-- hidden note -->"
            "<p>Bring&nbsp;a <b>photo</b> ID &amp; bag</p></body></html>")
    text = validate.html_to_text(page)
    assert "hidden note" not in text and "color" not in text
    assert text == "Bring a photo ID & bag"


def test_check_sources_reports_missing_quotes_and_hours_quote():
    place = good_place(
        eligibility=[{"type": "id_required", "source_quote": "Please bring a photo ID."},
                     {"type": "note", "text": "Appointment", "source_quote": "Call first."}],
        hours_quote="Mon 9 AM - 5 PM",
    )
    page = "<p>Please bring a photo ID.</p><p>Open Mon 8 AM - 4 PM.</p>"
    results = validate.check_sources([place], downloader=lambda url: page)
    errors, warnings = results[0]
    assert warnings == []
    assert len(errors) == 2
    assert any("Call first." in e for e in errors)
    assert any("hours_quote" in e for e in errors)


def test_check_sources_downloads_each_url_once():
    calls = []

    def fake(url):
        calls.append(url)
        return "<p>Please bring a photo ID. Mon 9 AM - 5 PM</p>"

    places = [good_place(id="a"), good_place(id="b")]
    results = validate.check_sources(places, downloader=fake)
    assert calls == ["https://example.org/pantry"]
    assert results[0] == ([], []) and results[1] == ([], [])


def test_download_failure_is_a_warning_that_lists_the_url():
    def broken(url):
        raise httpx.ConnectError("no route to host")

    errors, warnings = validate.check_sources([good_place()], downloader=broken)[0]
    assert errors == []
    assert len(warnings) == 1
    assert "https://example.org/pantry" in warnings[0]


def test_main_exit_code_without_network(tmp_path, capsys):
    good = tmp_path / "good.json"
    good.write_text(json.dumps([good_place()]), encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([good_place(lat=40.0)]), encoding="utf-8")
    assert validate.main([str(good)]) == 0
    assert validate.main([str(bad)]) == 1
    assert "outside the Houston bounds" in capsys.readouterr().out


def test_main_check_sources_fails_on_missing_quote(tmp_path, monkeypatch, capsys):
    path = tmp_path / "r.json"
    path.write_text(json.dumps([good_place()]), encoding="utf-8")
    monkeypatch.setattr(validate, "download_page", lambda url: "<p>Nothing relevant.</p>")
    assert validate.main([str(path), "--check-sources"]) == 1
    assert "not found on https://example.org/pantry" in capsys.readouterr().out


# ---------------------------------------------------------------- geocode

@pytest.fixture
def no_throttle(monkeypatch):
    """Nominatim waits one second between calls; tests skip the wait."""
    monkeypatch.setattr(geocode, "NOMINATIM_MIN_INTERVAL", 0)


def mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("place, expected", [
    ({"lat": None, "lng": None, "coords_source": None}, True),
    ({"lat": 29.7, "lng": -95.3, "coords_source": None}, True),
    ({"lat": 29.7, "lng": -95.3, "coords_source": "nominatim"}, True),
    ({"lat": 29.7, "lng": -95.3, "coords_source": "census"}, False),
    ({"lat": 29.7, "lng": -95.3, "coords_source": "manual"}, False),
])
def test_needs_geocode_follows_coords_source(place, expected):
    assert geocode.needs_geocode(place) is expected


def test_census_lookup_reads_x_as_lng_and_y_as_lat(no_throttle):
    seen = {}

    def handler(request):
        seen["params"] = dict(request.url.params)
        body = {"result": {"addressMatches": [
            {"coordinates": {"x": -95.3698, "y": 29.7604}}]}}
        return httpx.Response(200, json=body)

    with mock_client(handler) as client:
        assert geocode.census_lookup(client, "500 McKinney St, Houston, TX") == (29.7604, -95.3698)
    assert seen["params"]["benchmark"] == "Public_AR_Current"
    assert seen["params"]["format"] == "json"


def test_census_no_match_returns_none(no_throttle):
    with mock_client(lambda r: httpx.Response(200, json={"result": {"addressMatches": []}})) as client:
        assert geocode.census_lookup(client, "nowhere") is None


def test_nominatim_sends_user_agent_and_parses_lat_lon(no_throttle):
    seen = {}

    def handler(request):
        seen["agent"] = request.headers["User-Agent"]
        return httpx.Response(200, json=[{"lat": "29.7604", "lon": "-95.3698"}])

    with mock_client(handler) as client:
        assert geocode.nominatim_lookup(client, "x") == (29.7604, -95.3698)
    assert seen["agent"] == "HelpLineHouston/0.1 (hackathon)"


def test_census_success_sets_rounded_coords_and_source(monkeypatch, no_throttle):
    monkeypatch.setattr(geocode, "census_lookup", lambda client, address: (29.123456789, -95.987654321))

    def nominatim_must_not_run(client, address):
        raise AssertionError("nominatim should not be called")

    monkeypatch.setattr(geocode, "nominatim_lookup", nominatim_must_not_run)
    places = [{"id": "p", "address": "1 Main St", "lat": None, "lng": None, "coords_source": None}]
    stats = geocode.process_places(places, None, log=lambda *a: None)
    assert places[0]["lat"] == 29.12346
    assert places[0]["lng"] == -95.98765
    assert places[0]["coords_source"] == "census"
    assert stats["geocoded"] == 1 and stats["failed"] == []


def test_falls_back_to_nominatim_when_census_has_no_match(monkeypatch, no_throttle):
    monkeypatch.setattr(geocode, "census_lookup", lambda client, address: None)
    monkeypatch.setattr(geocode, "nominatim_lookup", lambda client, address: (29.7, -95.4))
    places = [{"id": "p", "address": "1 Main St", "lat": None, "lng": None, "coords_source": None}]
    geocode.process_places(places, None, log=lambda *a: None)
    assert places[0]["coords_source"] == "nominatim"
    assert (places[0]["lat"], places[0]["lng"]) == (29.7, -95.4)


def test_failed_place_is_left_unchanged_and_reported(monkeypatch, no_throttle):
    monkeypatch.setattr(geocode, "census_lookup", lambda client, address: None)
    monkeypatch.setattr(geocode, "nominatim_lookup", lambda client, address: None)
    place = {"id": "lost", "address": "Nowhere", "lat": None, "lng": None, "coords_source": None}
    before = dict(place)
    stats = geocode.process_places([place], None, log=lambda *a: None)
    assert place == before
    assert stats["failed"][0][0] == "lost"
    assert "no match" in stats["failed"][0][1]


def test_one_bad_place_does_not_stop_the_run(monkeypatch, no_throttle):
    def census(client, address):
        if address == "explode":
            raise RuntimeError("boom")
        return (29.7, -95.4)

    monkeypatch.setattr(geocode, "census_lookup", census)
    monkeypatch.setattr(geocode, "nominatim_lookup", lambda client, address: None)
    bad = {"id": "bad", "address": "explode", "lat": None, "lng": None, "coords_source": None}
    good = {"id": "good", "address": "fine", "lat": None, "lng": None, "coords_source": None}
    stats = geocode.process_places([bad, good], None, log=lambda *a: None)
    assert [pid for pid, _ in stats["failed"]] == ["bad"]
    assert good["coords_source"] == "census"


def test_manual_coordinates_are_not_touched(monkeypatch, no_throttle):
    def must_not_run(client, address):
        raise AssertionError("manual coordinates should not be looked up")

    monkeypatch.setattr(geocode, "census_lookup", must_not_run)
    monkeypatch.setattr(geocode, "nominatim_lookup", must_not_run)
    place = {"id": "m", "address": "1 Main", "lat": 29.75, "lng": -95.36, "coords_source": "manual"}
    stats = geocode.process_places([place], None, log=lambda *a: None)
    assert place["lat"] == 29.75 and stats["kept"] == 1


def test_dry_run_writes_nothing(tmp_path, monkeypatch, no_throttle):
    monkeypatch.setattr(geocode, "census_lookup", lambda client, address: (29.7, -95.4))
    path = tmp_path / "resources.json"
    original = [{"id": "p", "address": "1 Main", "lat": None, "lng": None, "coords_source": None}]
    path.write_text(json.dumps(original), encoding="utf-8")
    assert geocode.main([str(path), "--dry-run"]) == 0
    assert json.loads(path.read_text(encoding="utf-8")) == original


def test_main_writes_coords_and_keeps_other_fields(tmp_path, monkeypatch, no_throttle):
    monkeypatch.setattr(geocode, "census_lookup", lambda client, address: (29.7, -95.4))
    path = tmp_path / "resources.json"
    path.write_text(json.dumps([{"id": "p", "address": "1 Main", "name": "Keep Me",
                                 "lat": None, "lng": None, "coords_source": None}]), encoding="utf-8")
    assert geocode.main([str(path)]) == 0
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written[0]["name"] == "Keep Me"
    assert written[0]["lat"] == 29.7 and written[0]["coords_source"] == "census"


# ---------------------------------------------------------------- review fixes

def test_punctuation_after_inline_tag_still_matches():
    text = validate.html_to_text("<p>Please bring a <b>photo ID</b>.</p>")
    assert validate.quote_in_page("Please bring a photo ID.", text)


def test_tag_in_the_middle_of_a_word_stays_joined():
    text = validate.html_to_text("<p>Fr<b>ee</b> meals and <span>Photo</span><span>ID</span></p>")
    assert "Free meals" in text
    assert validate.quote_in_page("Free meals", text)
    assert validate.quote_in_page("PhotoID", text)


def test_adjacent_spans_join_into_one_word():
    text = validate.html_to_text("<span>Photo</span><span>ID</span>")
    assert text == "PhotoID"
    assert validate.quote_in_page("Photo ID", text)


def test_block_tags_still_separate_words():
    text = validate.html_to_text("<p>Mon</p><p>Tue</p><td>9</td><td>5</td>")
    assert text == "Mon Tue 9 5"


def test_changed_word_still_fails_to_match():
    text = validate.html_to_text("<p>Please bring a <b>photo ID</b>.</p>")
    assert not validate.quote_in_page("Please bring a passport.", text)
    assert not validate.quote_in_page("Please bring a photo license.", text)


def test_write_keeps_original_file_permissions(tmp_path, monkeypatch, no_throttle):
    import os
    monkeypatch.setattr(geocode, "census_lookup", lambda client, address: (29.7, -95.4))
    path = tmp_path / "resources.json"
    path.write_text(json.dumps([{"id": "p", "address": "1 Main", "lat": None, "lng": None,
                                 "coords_source": None}]), encoding="utf-8")
    os.chmod(path, 0o644)
    assert geocode.main([str(path)]) == 0
    assert (path.stat().st_mode & 0o777) == 0o644
