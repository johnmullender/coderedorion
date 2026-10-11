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
    world["resources"] = [make_place("a", 0, needs_review=True, last_verified=None)]
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


def test_directions_without_bus_says_so_with_miles(client, world):
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
