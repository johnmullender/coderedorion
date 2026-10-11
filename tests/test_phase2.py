"""Tests for phase 2 in main.py: eligibility questions, bus directions and their SMS limits.

No network and no Gemini: the brain is rule-based, the database is the in-memory fallback,
db.get_resources and transit.directions are replaced with small fakes.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import brain
import db
import eligibility
import location
import main
import matching
import transit

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
ALL_DAY = {day: ["00:00-24:00"] for day in DAYS}

ID_RULE = {"type": "id_required", "source_quote": "Please bring a photo ID."}
ROUTE = {
    "legs": [{"route": "54", "headsign": "Downtown", "board_stop": "Scott @ Holman",
              "alight_stop": "Main @ Gray", "departs": "16:12", "arrives": "16:31", "stops": 6}],
    "walk_to_min": 3, "walk_from_min": 2, "arrive_by": "16:33",
}


def make_place(pid, step=0, rules=None, need="food", **extra):
    """A small open place. Higher `step` means farther from the kiosk (distance order)."""
    place = {
        "id": pid, "name": f"Place {pid.upper()}", "category": [need],
        "address": f"{step + 1} Main St, Houston, TX 77004",
        "lat": round(29.72 + 0.01 * step, 4), "lng": -95.34,
        "coords_source": "manual", "hours": dict(ALL_DAY), "hours_quote": None,
        "eligibility": list(rules or []), "services": "", "languages": ["en", "es"],
        "phone": "713-555-0100", "notes": "", "source_url": "https://example.org/page",
        "last_verified": "2026-10-01", "needs_review": False,
        "hours_quote": "Open every day.",
    }
    place.update(extra)
    return place


@pytest.fixture
def world(monkeypatch):
    """An offline app: returns a state dict with the resource list and the fake bus answer."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(brain, "_client", None)
    monkeypatch.setattr(brain, "_get_client", lambda: None)
    monkeypatch.setattr(location, "geocode", lambda text: None)
    monkeypatch.setattr(db, "_memory_requests", [])
    monkeypatch.setattr(db, "_next_memory_id", 1)
    main.SESSIONS.clear()

    state = {"resources": [], "bus": None, "bus_calls": []}

    def fake_resources():
        return state["resources"]

    def fake_directions(from_lat, from_lng, to_lat, to_lng, when=None, source=None):
        state["bus_calls"].append((from_lat, from_lng, to_lat, to_lng))
        return state["bus"]

    monkeypatch.setattr(db, "get_resources", fake_resources)
    monkeypatch.setattr(transit, "directions", fake_directions)
    return state


@pytest.fixture
def client(world):
    with TestClient(main.app) as test_client:
        yield test_client


def ask(client, text, session_id="s1", channel="kiosk", **extra):
    body = {"channel": channel, "session_id": session_id, "text": text}
    body.update(extra)
    response = client.post("/ask", json=body)
    assert response.status_code == 200
    return response.json()


def ids(data):
    return [option["id"] for option in data["options"]]


# ---------------------------------------------------------------------------
# Eligibility questions
# ---------------------------------------------------------------------------

def test_question_asked_for_places_with_rules(client, world):
    world["resources"] = [make_place("a", 0, [ID_RULE]), make_place("b", 1)]
    data = ask(client, "I need food")
    assert data["asking"] == "id"
    assert data["reply"] == "Do you have a photo ID with you?"
    assert data["intent"] == "find" and data["unmet"] is False
    assert main.SESSIONS["s1"]["awaiting_answer"] == "id"
    assert main.SESSIONS["s1"]["asked"] == 1


def test_places_without_rules_get_no_questions(client, world):
    world["resources"] = [make_place("a", 0), make_place("b", 1), make_place("c", 2)]
    data = ask(client, "I need food")
    assert data["asking"] is None
    assert "Place A" in data["reply"]
    assert ids(data) == ["a", "b", "c"]
    assert main.SESSIONS["s1"]["awaiting_answer"] is None


def test_question_only_looks_at_top_three_places(client, world):
    world["resources"] = [make_place("a", 0), make_place("b", 1), make_place("c", 2),
                          make_place("d", 3, [ID_RULE])]
    data = ask(client, "I need food")
    assert data["asking"] is None  # the rule is on the fourth place only


def test_no_removes_the_place(client, world):
    world["resources"] = [make_place("a", 0, [ID_RULE]), make_place("b", 1), make_place("c", 2)]
    ask(client, "I need food")
    data = ask(client, "no")
    assert data["asking"] is None
    assert ids(data) == ["b", "c"]
    assert "Place A" not in data["reply"]


def test_skip_keeps_place_and_adds_reminder(client, world):
    world["resources"] = [make_place("a", 0, [ID_RULE]), make_place("b", 1)]
    ask(client, "I need food")
    data = ask(client, "skip")
    assert ids(data) == ["a", "b"]
    assert "Bring a photo ID if you have one." in data["reply"]


def test_yes_keeps_place_without_reminder(client, world):
    world["resources"] = [make_place("a", 0, [ID_RULE]), make_place("b", 1)]
    ask(client, "I need food")
    data = ask(client, "yes")
    assert ids(data) == ["a", "b"]
    assert "Bring a photo ID" not in data["reply"]


def test_at_most_three_questions(client, world):
    world["resources"] = [
        make_place("a", 0, [ID_RULE, {"type": "serves", "group": "women", "source_quote": "x"}]),
        make_place("b", 1, [{"type": "age", "min": 18, "max": 24, "source_quote": "x"},
                            {"type": "residency", "area": "Harris County", "source_quote": "x"}]),
        make_place("c", 2, [{"type": "income_limit", "question": "Is your household income under $2,600 a month?",
                             "source_quote": "x"}]),
    ]
    data = ask(client, "I need food")
    asked = [data["asking"]]
    for _ in range(2):
        data = ask(client, "yes")
        asked.append(data["asking"])
    assert asked == ["id", "serves:women", "age:18-24"]
    # Three questions used: the next one (residency / income) is not asked.
    data = ask(client, "yes")
    assert data["asking"] is None
    assert main.SESSIONS["s1"]["asked"] == 3
    assert ids(data) == ["a", "b", "c"]


def test_unclear_message_starts_a_new_search(client, world):
    world["resources"] = [make_place("a", 0, [ID_RULE]), make_place("s", 1, need="shelter")]
    first = ask(client, "I need food")
    assert first["asking"] == "id"
    data = ask(client, "I need shelter")
    assert data["need"] == "shelter"
    assert data["intent"] == "find"
    assert data["asking"] is None
    assert ids(data) == ["s"]
    assert main.SESSIONS["s1"]["awaiting_answer"] is None


def test_kiosk_yes_and_no_answer_questions(client, world):
    world["resources"] = [make_place("a", 0, [ID_RULE]), make_place("b", 1)]
    ask(client, "I need food", channel="kiosk")
    data = ask(client, "no", channel="kiosk")
    assert ids(data) == ["b"]
    ask(client, "I need food", session_id="k2", channel="kiosk")
    data = ask(client, "yes", session_id="k2", channel="kiosk")
    assert ids(data) == ["a", "b"]


def test_question_in_spanish(client, world):
    world["resources"] = [make_place("a", 0, [ID_RULE]), make_place("b", 1)]
    data = ask(client, "necesito comida", channel="kiosk", lang="es")
    assert data["language"] == "es"
    assert data["asking"] == "id"
    assert data["reply"] == "¿Tiene una identificación con foto?"


def test_next_option_still_works_while_asked(client, world):
    world["resources"] = [make_place("a", 0, [ID_RULE]), make_place("b", 1), make_place("c", 2)]
    ask(client, "I need food")
    data = ask(client, "2")  # not yes/no: it ends the question and picks option 2
    assert data["intent"] == "next_option"
    assert data["selected"] == 2
    assert "Place B" in data["reply"]
    assert main.SESSIONS["s1"]["awaiting_answer"] is None


def test_yes_without_question_is_small_talk(client, world):
    data = ask(client, "yes")
    assert data["intent"] == "other"


def test_zip_in_rule_filters_by_zip_silently(client, world):
    world["resources"] = [
        make_place("a", 0, [{"type": "zip_in", "zips": ["77021"], "source_quote": "x"}]),
        make_place("b", 1),
    ]
    data = ask(client, "I need food 77004", channel="sms")
    assert data["asking"] is None
    assert ids(data) == ["b"]


def test_confirm_hours_line_for_unverified_place(client, world):
    world["resources"] = [make_place("a", 0, needs_review=True, last_verified=None, hours_quote=None)]
    data = ask(client, "necesito comida", channel="kiosk", lang="es")
    assert "Llame para confirmar el horario: 713-555-0100." in data["reply"]


# ---------------------------------------------------------------------------
# Bus directions
# ---------------------------------------------------------------------------

def test_bus_line_and_transit_in_json(client, world):
    world["resources"] = [make_place("a", 0)]
    world["bus"] = ROUTE
    data = ask(client, "I need food")
    assert "54" in data["reply"] and "Downtown" in data["reply"]
    assert data["transit"]["legs"][0]["route"] == "54"
    assert world["bus_calls"] == [(location.STATION_LAT, location.STATION_LNG, 29.72, -95.34)]


def test_next_option_gets_bus_line(client, world):
    world["resources"] = [make_place("a", 0), make_place("b", 1)]
    world["bus"] = ROUTE
    ask(client, "I need food")
    data = ask(client, "2")
    assert data["selected"] == 2
    assert data["transit"]["legs"][0]["route"] == "54"
    assert world["bus_calls"][-1][2:] == (pytest.approx(29.73), -95.34)


def test_sms_fits_with_bus_line(client, world):
    world["resources"] = [make_place("a", 0)]
    world["bus"] = ROUTE
    data = ask(client, "I need food 77004", channel="sms")
    assert "54" in data["reply"]
    assert "Reply BUS" not in data["reply"]
    assert len(data["reply"]) <= 300


def test_sms_drops_bus_line_first(client, world):
    # About 175 characters: with the bus line the reply is over 300, without it the notes fit.
    note = ("Bring a bag and a copy of any mail you have, and ask the front desk staff for help "
            "with the intake forms before you go home today, and bring a friend if you can.")
    world["resources"] = [make_place("a", 0, [{"type": "note", "text": note, "source_quote": "x"}])]
    world["bus"] = ROUTE
    data = ask(client, "I need food 77004", channel="sms")
    assert len(data["reply"]) <= 300
    assert "Reply BUS for directions." in data["reply"]
    assert "54" not in data["reply"]
    assert "Bring a bag and a copy" in data["reply"]
    assert data["transit"]["legs"][0]["route"] == "54"


def test_sms_drops_notes_after_bus_line(client, world):
    long_note = "Bring a bag and a copy of your lease " + "and a friend " * 20 + "to the front desk."
    world["resources"] = [make_place("a", 0, [{"type": "note", "text": long_note, "source_quote": "x"}])]
    world["bus"] = ROUTE
    data = ask(client, "I need food 77004", channel="sms")
    assert len(data["reply"]) <= 300
    assert "Reply BUS for directions." in data["reply"]
    assert "front desk" not in data["reply"]


def test_directions_intent_uses_selected_option(client, world):
    world["resources"] = [make_place("a", 0), make_place("b", 1)]
    world["bus"] = ROUTE
    ask(client, "I need food")
    ask(client, "2")
    data = ask(client, "how do I get there?")
    assert data["intent"] == "directions"
    assert data["selected"] == 2
    assert data["transit"]["legs"][0]["route"] == "54"
    assert "54" in data["reply"]
    assert world["bus_calls"][-1][2] == pytest.approx(29.73)


def test_directions_without_bus_says_so_with_miles(client, world, monkeypatch):
    monkeypatch.setattr(main, "bus_data_available", lambda: True)
    world["resources"] = [make_place("a", 0)]
    world["bus"] = None
    ask(client, "I need food")
    data = ask(client, "how do I get there?")
    assert data["intent"] == "directions"
    assert data["transit"] is None
    assert "no direct bus" in data["reply"].lower()
    assert "miles" in data["reply"]


def test_bare_bus_word_is_directions(client, world):
    world["resources"] = [make_place("a", 0)]
    world["bus"] = ROUTE
    ask(client, "I need food")
    data = ask(client, "bus")
    assert data["intent"] == "directions"
    assert data["transit"] is not None
    data = ask(client, "autobús")
    assert data["intent"] == "directions"


def test_directions_without_options(client):
    data = ask(client, "how do I get there?")
    assert data["intent"] == "directions"
    assert data["options"] == [] and data["transit"] is None


# ---------------------------------------------------------------------------
# Ruled out, bad coordinates, privacy, shape
# ---------------------------------------------------------------------------

def test_all_places_ruled_out(client, world):
    world["resources"] = [make_place("a", 0, [ID_RULE]), make_place("b", 1, [ID_RULE])]
    ask(client, "I need food")
    data = ask(client, "no")
    assert data["intent"] == "find"
    assert data["unmet"] is True
    assert data["options"] == [] and data["selected"] == 0
    assert data["asking"] is None
    assert "None of the open places fit your answers." in data["reply"]
    assert "211" in data["reply"]


def test_null_coordinates_are_skipped(client, world):
    world["resources"] = [
        make_place("nolat", 0, lat=None),
        make_place("nolng", 0, lng=None),
        make_place("good", 1),
    ]
    data = ask(client, "I need food")
    assert ids(data) == ["good"]
    assert data["reply"]


def test_answers_not_in_request_log(client, world):
    world["resources"] = [make_place("a", 0, [ID_RULE]), make_place("b", 1)]
    ask(client, "I need food", channel="kiosk")
    ask(client, "yes", channel="kiosk")
    ask(client, "no", channel="kiosk")
    body = client.get("/api/requests").json()
    assert body["counts"] == {"food": 1}
    assert len(body["requests"]) == 1
    row = body["requests"][0]
    assert row["text"] == "I need food"
    assert set(row) == {"id", "time", "channel", "need", "language", "lat", "lng",
                        "matched", "place", "text"}
    assert "eligibility" not in client.get("/api/requests").text
    assert "answers" not in client.get("/api/requests").text


def test_response_has_asking_and_transit_keys(client, world):
    world["resources"] = [make_place("a", 0)]
    for text in ("hello", "I need food", "ayuda"):
        data = ask(client, text)
        assert "asking" in data and "transit" in data
    data = client.post("/sms", data={"From": "+17135550199", "Body": "hi"})
    assert data.status_code == 200


def test_main_asks_matching_for_eight_places(client, world, monkeypatch):
    seen = []
    original = matching.find_matches

    def recording(*args, **kwargs):
        seen.append(kwargs.get("limit"))
        return original(*args, **kwargs)

    monkeypatch.setattr(main.matching, "find_matches", recording)
    world["resources"] = [make_place("p%d" % i, i) for i in range(10)]
    data = ask(client, "I need food")
    assert seen == [8]
    assert ids(data) == ["p0", "p1", "p2"]


def test_find_matches_skips_null_coordinates():
    now = datetime(2026, 10, 12, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
    resources = [make_place("nolat", 0, lat=None), make_place("nolng", 0, lng=None),
                 make_place("good", 1)]
    found = matching.find_matches("food", 29.72, -95.34, when=now, resources=resources)
    assert [m["id"] for m in found["options"]] == ["good"]
    none_open = matching.find_matches("food", 29.72, -95.34, when=now,
                                      resources=[make_place("nolat", 0, lat=None)])
    assert none_open["options"] == [] and none_open["next_open"] is None


def test_find_matches_keeps_nearest_eight():
    now = datetime(2026, 10, 12, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
    resources = [make_place("p%d" % i, i) for i in range(12)]
    found = matching.find_matches("food", 29.72, -95.34, when=now, resources=resources, limit=8)
    assert len(found["options"]) == 8
    assert found["options"][0]["id"] == "p0"


# ---------------------------------------------------------------------------
# Review fixes: answer detection, no-hours fallback, wording, zip, notes, hints, no-bus text
# ---------------------------------------------------------------------------

def test_no_with_new_need_starts_new_search(client, world):
    world["resources"] = [make_place("a", 0, [ID_RULE]), make_place("b", 1)]
    ask(client, "I need food")
    data = ask(client, "no I need food near 77004", channel="sms")
    assert data["intent"] == "find"
    # The "no" was not an answer: the search started again and asks the same question.
    assert data["asking"] == "id"
    assert main.SESSIONS["s1"]["answers"] == {}


def test_unclear_long_message_asks_the_question_again(client, world):
    world["resources"] = [make_place("a", 0, [ID_RULE]), make_place("b", 1)]
    ask(client, "I need food")
    data = ask(client, "I have no idea what to say to you right now")
    assert data["asking"] == "id"
    assert main.SESSIONS["s1"]["awaiting_answer"] == "id"
    assert main.SESSIONS["s1"]["answers"] == {}


def test_longer_answer_counts_by_its_first_word(client, world):
    world["resources"] = [make_place("a", 0, [ID_RULE]), make_place("b", 1)]
    ask(client, "I need food")
    data = ask(client, "no I do not have one")
    assert main.SESSIONS["s1"]["answers"] == {"id": False}
    assert ids(data) == ["b"]


def test_short_no_is_still_an_answer(client, world):
    world["resources"] = [make_place("a", 0, [ID_RULE]), make_place("b", 1)]
    ask(client, "I need food")
    data = ask(client, "no")
    assert ids(data) == ["b"]


def test_no_hours_places_fill_short_options(client, world):
    world["resources"] = [make_place("a", 0),
                          make_place("u1", 1, hours={d: [] for d in DAYS}),
                          make_place("u2", 2, hours={d: [] for d in DAYS})]
    data = ask(client, "I need food")
    assert ids(data) == ["a", "u1", "u2"]
    assert "hours_unknown" not in data["options"][0]
    fallback = data["options"][1]
    assert fallback["open_now"] is False
    assert data["options"][1]["id"] == "u1"
    assert data["unmet"] is False


def test_only_no_hours_places_never_say_open(client, world):
    world["resources"] = [make_place("u1", 0, hours={d: [] for d in DAYS}, phone="713-555-0111")]
    for channel in ("kiosk", "sms"):
        text = "I need food" if channel == "kiosk" else "I need food 77004"
        data = ask(client, text, session_id="u-" + channel, channel=channel)
        assert data["options"][0]["hours_unknown"] is True
        assert data["options"][0]["open_now"] is False
        assert data["unmet"] is True
        assert "hours not listed" in data["reply"]
        assert "call 713-555-0111 first" in data["reply"]
        assert "open now" not in data["reply"].lower()
        assert "Call to confirm hours" not in data["reply"]


def test_unknown_hours_wording_in_every_language(monkeypatch):
    monkeypatch.setattr(brain, "_client", None)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    place = dict(make_place("u", 0, hours={d: [] for d in DAYS}), hours_unknown=True,
                 open_now=False, distance_mi=0.8, closes_at=None, opens_at=None)
    expected = {"en": "hours not listed", "es": "horario no publicado", "vi": "chưa có giờ mở cửa",
                "zh": "未列出营业时间", "ar": "ساعات العمل غير مدرجة"}
    banned = {"en": "open now", "es": "abierto ahora", "vi": "đang mở cửa", "zh": "现在开放",
              "ar": "مفتوح الآن"}
    for language in expected:
        for channel in ("kiosk", "sms"):
            reply = brain.answer("food", language, "food", [place], channel)
            assert expected[language] in reply, (language, channel)
            assert banned[language] not in reply, (language, channel)


def test_gemini_prompt_knows_about_unknown_hours():
    assert "hours_unknown" in brain.FACT_FIELDS
    assert "hours_unknown" in brain.ANSWER_PROMPT and "Never say it is open" in brain.ANSWER_PROMPT


def test_confirm_line_only_for_empty_hours_or_missing_quote(client, world):
    world["resources"] = [make_place("a", 0, needs_review=True, hours_quote="Open every day.")]
    data = ask(client, "I need food", session_id="q1")
    assert "Call to confirm hours" not in data["reply"]
    world["resources"] = [make_place("b", 0, needs_review=True, hours_quote=None)]
    data = ask(client, "I need food", session_id="q2")
    assert "Call to confirm hours: 713-555-0100." in data["reply"]


def test_zip_note_when_zip_is_unknown(client, world):
    rule = {"type": "zip_in", "zips": ["77004", "77021"], "source_quote": "x"}
    world["resources"] = [make_place("a", 0, [rule])]
    data = ask(client, "I need food")  # kiosk: no zip anywhere
    assert "Serves zip codes: 77004, 77021." in data["reply"]


def test_zip_note_in_every_language():
    place = {"eligibility": [{"type": "zip_in", "zips": ["77004", "77021"], "source_quote": "x"}]}
    for language in eligibility.LANGUAGES:
        notes = eligibility.notes_for(place, {}, language)
        assert len(notes) == 1 and "77004, 77021" in notes[0], language
    assert eligibility.notes_for(place, {}, "en", zip_code="77004") == []


def test_zip_comes_from_resolved_location(client, world):
    table = location.load_zip_table()
    zip_code = "77021"
    assert main.zip_for_coords(table[zip_code]["lat"], table[zip_code]["lng"]) == zip_code
    rule = {"type": "zip_in", "zips": ["77004"], "source_quote": "x"}
    world["resources"] = [make_place("a", 0, [rule]), make_place("b", 1)]
    data = ask(client, "I need food", lat=table[zip_code]["lat"], lng=table[zip_code]["lng"])
    assert ids(data) == ["b"]
    assert data["asking"] is None


def test_notes_get_full_stops_and_spaces(client, world):
    notes = [{"type": "note", "text": "Bring a bag", "source_quote": "x"},
             {"type": "note", "text": "Ask at the desk", "source_quote": "x"}]
    world["resources"] = [make_place("a", 0, notes)]
    data = ask(client, "I need food")
    assert "Bring a bag. Ask at the desk." in data["reply"]


def test_reply_hint_matches_option_count_in_every_language(monkeypatch):
    monkeypatch.setattr(brain, "_client", None)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    two = [dict(make_place("x", 0), distance_mi=0.2, open_now=True, closes_at=None, opens_at=None),
           dict(make_place("y", 1), distance_mi=0.4, open_now=True, closes_at=None, opens_at=None)]
    for language in ("en", "es", "vi", "zh", "ar"):
        reply = brain.answer("food", language, "food", two, "sms")
        assert brain.reply_hint(language, 2) in reply
        assert brain.reply_hint(language, 3) not in reply
    assert brain.reply_hint("en", 2) == "Reply 2 for another option."
    assert brain.reply_hint("en", 3) == "Reply 2 or 3 for other options."


def test_sms_hint_for_two_options_end_to_end(client, world):
    world["resources"] = [make_place("a", 0), make_place("b", 1)]
    data = ask(client, "I need food 77004", channel="sms")
    assert "Reply 2 for another option." in data["reply"]
    assert "2 or 3" not in data["reply"]


def test_distance_phrase_singular_and_plural():
    assert main.distance_phrase("en", 1.0) == "1 mile away"
    assert main.distance_phrase("en", 0.8) == "0.8 miles away"
    assert main.distance_phrase("es", 1.0) == "a 1 milla"
    assert main.distance_phrase("es", 0.8) == "a 0.8 millas"


def test_bus_times_unavailable_when_no_bus_data(client, world, monkeypatch):
    monkeypatch.setattr(main, "bus_data_available", lambda: False)
    world["resources"] = [make_place("a", 0)]
    world["bus"] = None
    ask(client, "I need food")
    data = ask(client, "how do I get there?")
    assert "Bus times aren't available right now" in data["reply"]
    assert "no direct bus" not in data["reply"].lower()


def test_all_ruled_out_then_next_place_to_open(client, world):
    tomorrow = DAYS[(datetime.now(ZoneInfo("America/Chicago")).weekday() + 1) % 7]
    later_hours = {day: [] for day in DAYS}
    later_hours[tomorrow] = ["00:00-24:00"]
    world["resources"] = [make_place("a", 0, [ID_RULE]),
                          make_place("later", 1, hours=later_hours, name="Later Place")]
    ask(client, "I need food")
    data = ask(client, "no")
    assert data["unmet"] is True and data["options"] == []
    assert data["reply"].startswith("None of the open places fit your answers.")
    assert "The next one to open is Later Place" in data["reply"]
    assert "Nothing for" not in data["reply"]
    assert "211" not in data["reply"]


def test_all_ruled_out_without_next_place_points_to_211(client, world):
    world["resources"] = [make_place("a", 0, [ID_RULE])]
    ask(client, "I need food")
    data = ask(client, "no")
    assert data["reply"].startswith("None of the open places fit your answers.")
    assert "211" in data["reply"]
    assert "could not find" not in data["reply"]


# ---------------------------------------------------------------------------
# Offers line (kiosk and SMS)
# ---------------------------------------------------------------------------

def offers(*keys):
    """The offers list a place has: each key with a placeholder quote and url."""
    return [{"key": key, "quote": "Some quote", "url": "https://example.org/offers"} for key in keys]


def test_offers_line_comes_after_the_main_line(client, world):
    world["resources"] = [make_place("a", 0, offers=offers("showers", "wifi", "food_pantry"))]
    data = ask(client, "I need food", channel="kiosk")
    assert "Offers: showers, Wi-Fi, food pantry." in data["reply"]
    assert data["reply"].index("Offers:") > data["reply"].index("Place A")


def test_offers_line_lists_at_most_four(client, world):
    keys = ["laundry", "mail", "showers", "wifi", "restrooms", "clothing"]
    world["resources"] = [make_place("a", 0, offers=offers(*keys))]
    data = ask(client, "I need food", channel="kiosk")
    assert "Offers: laundry, mail, showers, Wi-Fi." in data["reply"]
    assert "restrooms" not in data["reply"] and "clothing" not in data["reply"]


def test_offers_line_in_spanish(client, world):
    world["resources"] = [make_place("a", 0, offers=offers("showers", "wifi"))]
    data = ask(client, "necesito comida", channel="kiosk", lang="es")
    assert "Ofrece: duchas, Wi-Fi." in data["reply"]


def test_offers_line_is_not_in_the_reply_for_places_without_offers(client, world):
    world["resources"] = [make_place("a", 0)]
    data = ask(client, "I need food", channel="kiosk")
    assert "Offers" not in data["reply"]


def test_sms_notes_are_dropped_before_the_offers_line(client, world):
    # About 200 characters of notes: with the offers line the reply is over 300, so the notes go
    # and the offers line stays. Without the offers line the notes would have fitted.
    note = ("Bring a bag and a copy of any mail you have, and ask the front desk staff for help "
            "with the intake forms before you go home today. Ask for Maria at the desk. "
            "Bring a friend if you can, and a snack for the trip.")
    assert 190 < len(note) < 215
    world["resources"] = [make_place("a", 0, [{"type": "note", "text": note, "source_quote": "x"}],
                                     offers=offers("showers", "wifi"))]
    data = ask(client, "I need food 77004", channel="sms")
    assert len(data["reply"]) <= 300
    assert "Offers: showers, Wi-Fi." in data["reply"]
    assert "Bring a bag and a copy" not in data["reply"]
    world["resources"] = [make_place("a", 0, [{"type": "note", "text": note, "source_quote": "x"}])]
    data = ask(client, "I need food 77004", channel="sms", session_id="s2")
    assert "Bring a bag and a copy" in data["reply"]  # no offers: the notes fit


def test_sms_offers_line_kept_with_short_notes(client, world):
    world["resources"] = [make_place("a", 0, [{"type": "note", "text": "Bring a bag.", "source_quote": "x"}],
                                     offers=offers("showers", "wifi"))]
    data = ask(client, "I need food 77004", channel="sms")
    assert "Offers: showers, Wi-Fi." in data["reply"]
    assert "Bring a bag." in data["reply"]
    assert len(data["reply"]) <= 300


def test_sms_reply_hint_stays_last_after_the_offers_line(client, world):
    world["resources"] = [make_place("a", 0, offers=offers("showers")), make_place("b", 1, offers=offers("wifi"))]
    data = ask(client, "I need food 77004", channel="sms")  # place B is nearer, so it is the one described
    assert data["reply"].endswith("Reply 2 for another option.")
    assert data["reply"].index("Offers: Wi-Fi.") < data["reply"].index("Reply 2 for another option.")
    assert len(data["reply"]) <= 300


def test_sms_offers_line_dropped_when_the_main_line_is_long(client, world):
    long_name = "Houston Community Services and Family Support Center for Adults " * 3
    world["resources"] = [make_place("a", 0, offers=offers("showers", "wifi"), name=long_name)]
    data = ask(client, "I need food 77004", channel="sms")
    assert len(data["reply"]) <= 300
    assert data["reply"]  # never empty, never over the limit


def test_offers_on_next_option_reply(client, world):
    world["resources"] = [make_place("a", 0), make_place("b", 1, offers=offers("hot_meals"))]
    ask(client, "I need food")
    data = ask(client, "2")
    assert data["selected"] == 2
    assert "Offers: free meals." in data["reply"]


# ---------------------------------------------------------------------------
# Nothing open right now
# ---------------------------------------------------------------------------

def tomorrow_only_hours():
    """Open all day tomorrow and never today, so the place is closed now and opens soonest."""
    tomorrow = DAYS[(datetime.now(ZoneInfo("America/Chicago")).weekday() + 1) % 7]
    hours = {day: [] for day in DAYS}
    hours[tomorrow] = ["00:00-24:00"]
    return hours


def test_nothing_open_leads_with_the_next_place_to_open(client, world):
    world["resources"] = [make_place("later", 1, hours=tomorrow_only_hours(), name="Later Place")]
    data = ask(client, "I need food", channel="kiosk")
    assert data["reply"].startswith("Nothing nearby is open right now.")
    assert "The next one to open is Later Place" in data["reply"]
    assert "It is " in data["reply"] and "miles away." in data["reply"]  # its distance
    assert data["unmet"] is True
    assert ids(data) == ["later"] and data["selected"] == 1


def test_nothing_open_then_places_with_no_hours_listed(client, world):
    world["resources"] = [make_place("later", 1, hours=tomorrow_only_hours(), name="Later Place"),
                          make_place("u1", 2, hours={d: [] for d in DAYS}, name="Unknown Place",
                                     phone="713-555-0111")]
    data = ask(client, "I need food", channel="kiosk")
    assert data["reply"].startswith("Nothing nearby is open right now.")
    assert "The next one to open is Later Place" in data["reply"]
    assert "Turn the knob to option 2 for a nearby place with no listed hours." in data["reply"]
    assert "Unknown Place" not in data["reply"]  # option 2 is described only when it is chosen
    assert ids(data) == ["later", "u1"]
    assert data["unmet"] is True
    assert "open now" not in data["reply"].lower()


def test_nothing_open_sms_keeps_the_lead_and_fits(client, world):
    world["resources"] = [make_place("later", 1, hours=tomorrow_only_hours(), name="Later Place"),
                          make_place("u1", 2, hours={d: [] for d in DAYS}, name="Unknown Place")]
    data = ask(client, "I need food 77004", channel="sms")
    assert data["reply"].startswith("Nothing nearby is open right now.")
    assert "The next one to open is Later Place" in data["reply"]
    assert data["reply"].endswith("Reply 2 for a nearby place with no listed hours.")
    assert len(data["reply"]) <= 300


def test_nothing_open_sms_drops_the_hint_first_when_long(client, world):
    long_name = "Later Place " + "Community Clinic and Services " * 5
    world["resources"] = [make_place("later", 1, hours=tomorrow_only_hours(), name=long_name,
                                     address="1 Long Road " * 6, offers=offers("showers", "wifi")),
                          make_place("u1", 2, hours={d: [] for d in DAYS}, name="Unknown Place")]
    data = ask(client, "I need food 77004", channel="sms")
    assert data["reply"].startswith("Nothing nearby is open right now.")
    assert len(data["reply"]) <= 300
    assert "Reply 2 for a nearby place" not in data["reply"]
    assert ids(data) == ["later", "u1"]


def test_choosing_option_one_after_nothing_open_describes_the_next_place(client, world):
    world["resources"] = [make_place("later", 1, hours=tomorrow_only_hours(), name="Later Place"),
                          make_place("u1", 2, hours={d: [] for d in DAYS}, name="Unknown Place")]
    ask(client, "I need food", channel="kiosk")
    data = ask(client, "1", channel="kiosk")
    assert data["selected"] == 1
    assert "The next one to open is Later Place" in data["reply"]
    assert "open now" not in data["reply"].lower()
    data = ask(client, "how do I get there?", channel="kiosk")
    assert data["intent"] == "directions" and "Later Place" in data["reply"]


def test_choosing_option_two_describes_the_place_with_no_hours(client, world):
    world["resources"] = [make_place("later", 1, hours=tomorrow_only_hours(), name="Later Place"),
                          make_place("u1", 2, hours={d: [] for d in DAYS}, name="Unknown Place",
                                     phone="713-555-0111")]
    ask(client, "I need food", channel="kiosk")
    data = ask(client, "2", channel="kiosk")
    assert data["selected"] == 2
    assert "Unknown Place" in data["reply"] and "hours not listed" in data["reply"]
    assert "open now" not in data["reply"].lower()


def test_nothing_open_and_nothing_listed_points_to_211(client, world):
    world["resources"] = []
    data = ask(client, "I need food", channel="kiosk")
    assert "211" in data["reply"]
    assert data["unmet"] is True
    assert "Nothing nearby" not in data["reply"]


# ---------------------------------------------------------------------------
# Bus directions: the place name, the nearest stops, the Maps link, the next day's bus
# ---------------------------------------------------------------------------

ROUTE_LATER = dict(ROUTE, later=True)


def stops_fake(monkeypatch, names=("Scott@Holman", "Main@Gray")):
    """Pretend the schedule knows the stops: returns the names for every call (or None when names is None)."""
    calls = []

    def fake_closest(from_lat, from_lng, to_lat, to_lng, language, channel, source=None):
        calls.append((from_lat, from_lng, to_lat, to_lng, language, channel))
        return names

    monkeypatch.setattr(transit, "closest_stops", fake_closest)
    return calls


def test_directions_reply_names_the_place(client, world):
    world["resources"] = [make_place("a", 0, name="Sample Pantry")]
    world["bus"] = ROUTE
    ask(client, "I need food")
    data = ask(client, "how do I get there?")
    assert data["reply"].startswith("To Sample Pantry: ")


def test_later_bus_is_said_to_be_the_first_one(client, world):
    world["resources"] = [make_place("a", 0, name="Sample Pantry")]
    world["bus"] = ROUTE_LATER
    ask(client, "I need food")
    data = ask(client, "how do I get there?")
    assert data["reply"].startswith("To Sample Pantry: No buses are running now. The first one:")
    assert data["transit"]["later"] is True


def test_no_route_says_nearest_stops_by_name_on_sms_with_map_link(client, world, monkeypatch):
    monkeypatch.setattr(main, "bus_data_available", lambda: True)
    calls = stops_fake(monkeypatch)
    world["resources"] = [make_place("a", 0, name="Sample Pantry", lat=29.7300, lng=-95.3500)]
    world["bus"] = None
    ask(client, "I need food 77004", channel="sms")
    data = ask(client, "how do I get there?", channel="sms")
    assert data["transit"] is None
    assert "No direct bus or transfer found to Sample Pantry" in data["reply"]
    assert "Nearest stop to you: Scott@Holman." in data["reply"]
    assert "Nearest stop to Sample Pantry: Main@Gray." in data["reply"]
    link = data["reply"].split("Google Maps transit route: ")[1]
    origin = calls[-1]
    assert link.startswith("https://www.google.com/maps/dir/?api=1&origin=")
    assert link == (f"https://www.google.com/maps/dir/?api=1&origin={origin[0]:.6f},{origin[1]:.6f}"
                    f"&destination=29.730000,-95.350000&travelmode=transit")
    assert len(data["reply"]) <= 300


def test_no_route_on_kiosk_names_stops_without_a_link(client, world, monkeypatch):
    monkeypatch.setattr(main, "bus_data_available", lambda: True)
    stops_fake(monkeypatch)
    world["resources"] = [make_place("a", 0, name="Sample Pantry")]
    world["bus"] = None
    ask(client, "I need food")
    data = ask(client, "how do I get there?", channel="kiosk")
    assert "Nearest stop to you: Scott@Holman." in data["reply"]
    assert "http" not in data["reply"]


def test_no_route_without_stops_still_names_the_place(client, world, monkeypatch):
    monkeypatch.setattr(main, "bus_data_available", lambda: True)
    stops_fake(monkeypatch, names=None)
    world["resources"] = [make_place("a", 0, name="Sample Pantry")]
    world["bus"] = None
    ask(client, "I need food")
    data = ask(client, "how do I get there?", channel="kiosk")
    assert "No direct bus or transfer found to Sample Pantry" in data["reply"]
    assert "Nearest stop" not in data["reply"]


def test_bus_times_unavailable_names_the_place_and_links_on_sms(client, world, monkeypatch):
    monkeypatch.setattr(main, "bus_data_available", lambda: False)
    world["resources"] = [make_place("a", 0, name="Sample Pantry")]
    world["bus"] = None
    ask(client, "I need food 77004", channel="sms")
    data = ask(client, "how do I get there?", channel="sms")
    assert data["reply"].startswith("Bus times aren't available right now. Sample Pantry is ")
    assert "https://www.google.com/maps/dir/?api=1&origin=" in data["reply"]
    assert len(data["reply"]) <= 300
    ask(client, "I need food", channel="kiosk", session_id="k9")
    kiosk = ask(client, "how do I get there?", channel="kiosk", session_id="k9")
    assert "Bus times aren't available right now. Sample Pantry is " in kiosk["reply"]
    assert "http" not in kiosk["reply"]


def test_long_place_name_keeps_the_map_link_inside_sms_limit(client, world, monkeypatch):
    monkeypatch.setattr(main, "bus_data_available", lambda: True)
    stops_fake(monkeypatch, names=("A very long stop name " * 4, "Another long stop name " * 4))
    world["resources"] = [make_place("a", 0, name="Name " * 60)]
    world["bus"] = None
    ask(client, "I need food 77004", channel="sms")
    data = ask(client, "how do I get there?", channel="sms")
    assert len(data["reply"]) <= 300
    assert data["reply"].endswith("travelmode=transit")


def test_no_route_message_wording_in_every_language(monkeypatch):
    monkeypatch.setattr(brain, "_client", None)
    for language in ["en", "es", "vi", "zh", "ar"]:
        for key in ["no_bus", "no_bus_stops", "bus_unavailable", "to_place", "map_link", "nothing_open"]:
            text = main.message(language, key, name="Pantry", dist="0.2 miles away", start="A", end="B",
                                text="Bus", link="https://example.org")
            assert "{" not in text and text.strip(), (language, key)
