"""Tests for main.py: the whole /ask flow, /sms, /api/requests, /tts and /stt.

Gemini is turned off (rule-based brain), the database is turned off (in-memory
log), the street geocoder is turned off (no network), and matching.find_matches
is replaced with a fake so results don't depend on the time of day.
"""

import pytest
from fastapi.testclient import TestClient

import brain
import db
import location
import main

PLACES = [
    {"id": "a", "name": "Pantry A", "address": "1 A St", "distance_mi": 0.5, "open_now": True,
     "closes_at": "18:00", "opens_at": None, "phone": "713-555-0001", "category": ["food"],
     "requirements": {}, "notes": "", "hours": {}},
    {"id": "b", "name": "Pantry B", "address": "2 B St", "distance_mi": 1.2, "open_now": True,
     "closes_at": "17:00", "opens_at": None, "phone": "713-555-0002", "category": ["food"],
     "requirements": {}, "notes": "", "hours": {}},
    {"id": "c", "name": "Pantry C", "address": "3 C St", "distance_mi": 2.0, "open_now": True,
     "closes_at": "16:00", "opens_at": None, "phone": "713-555-0003", "category": ["food"],
     "requirements": {}, "notes": "", "hours": {}},
]

NEXT_OPEN = dict(PLACES[0], open_now=False, closes_at=None, opens_at="Mon 09:00")


@pytest.fixture
def calls(monkeypatch):
    """Set up a clean, offline app. Returns the list of find_matches calls."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(brain, "_get_client", lambda: None)
    monkeypatch.setattr(location, "geocode", lambda text: None)
    monkeypatch.setattr(db, "_memory_requests", [])
    monkeypatch.setattr(db, "_next_memory_id", 1)
    main.SESSIONS.clear()

    recorded = []

    def fake_find(need, lat, lng, when=None, resources=None, limit=3):
        recorded.append({"need": need, "lat": lat, "lng": lng})
        return {"options": [dict(p) for p in PLACES], "next_open": None}

    monkeypatch.setattr(main.matching, "find_matches", fake_find)
    return recorded


@pytest.fixture
def client(calls):
    with TestClient(main.app) as test_client:
        yield test_client


def ask(client, text, session_id="s1", channel="sms", **extra):
    body = {"channel": channel, "session_id": session_id, "text": text}
    body.update(extra)
    response = client.post("/ask", json=body)
    assert response.status_code == 200
    return response.json()


def test_health(client):
    assert client.get("/").json() == {"ok": True, "service": "HelpLine Houston"}


def test_kiosk_find_returns_options(client, calls):
    data = ask(client, "I need food", channel="kiosk", lang="en")
    assert data["intent"] == "find"
    assert data["need"] == "food"
    assert data["selected"] == 1
    assert data["unmet"] is False and data["ask_location"] is False
    assert [o["n"] for o in data["options"]] == [1, 2, 3]
    assert set(data["options"][0]) == {"n", "id", "name", "address", "distance_mi",
                                       "open_now", "closes_at", "opens_at", "phone"}
    assert set(data) == {"reply", "language", "need", "intent", "options",
                         "selected", "unmet", "ask_location"}
    # The kiosk uses the station's fixed spot.
    assert calls[0]["lat"] == location.STATION_LAT and calls[0]["lng"] == location.STATION_LNG
    assert "Pantry A" in data["reply"]


def test_kiosk_uses_given_lat_lng(client, calls):
    ask(client, "I need food", channel="kiosk", lat=29.7, lng=-95.3)
    assert calls[0]["lat"] == 29.7 and calls[0]["lng"] == -95.3


def test_sms_pick_option_two_without_new_search(client, calls):
    ask(client, "I need food near 77004")
    assert len(calls) == 1
    data = ask(client, "2")
    assert data["intent"] == "next_option"
    assert data["selected"] == 2
    assert "Pantry B" in data["reply"]
    assert len(calls) == 1  # no new search
    assert len(data["options"]) == 3


def test_sms_option_out_of_range(client, calls):
    ask(client, "I need food near 77004")
    data = ask(client, "5")
    assert data["selected"] == 1
    assert "1 to 3" in data["reply"]


def test_sms_option_with_no_options(client):
    data = ask(client, "2")
    assert data["options"] == [] and data["selected"] == 0


def test_sms_asks_for_location_then_zip_completes(client, calls):
    data = ask(client, "I need food")
    assert data["ask_location"] is True
    assert data["need"] == "food"
    assert data["options"] == []
    assert calls == []

    data = ask(client, "77004")
    assert data["ask_location"] is False
    assert data["need"] == "food"
    assert data["selected"] == 1
    assert len(calls) == 1 and calls[0]["need"] == "food"
    assert calls[0]["lat"] == pytest.approx(29.73)


def test_sms_ask_location_in_spanish(client):
    data = ask(client, "necesito comida")
    assert data["ask_location"] is True
    assert data["language"] == "es"
    assert "código postal" in data["reply"]
    # The bare zip keeps the Spanish conversation going.
    data = ask(client, "77004")
    assert data["language"] == "es"


def test_unmet_path(client, monkeypatch):
    monkeypatch.setattr(main.matching, "find_matches",
                        lambda *a, **k: {"options": [], "next_open": dict(NEXT_OPEN)})
    data = ask(client, "I need food", channel="kiosk")
    assert data["unmet"] is True
    assert data["options"] == []
    assert data["selected"] == 0
    rows = client.get("/api/requests").json()
    assert rows["unmet_counts"] == {"food": 1}
    assert rows["requests"][-1]["matched"] is False
    assert rows["requests"][-1]["place"] is None


def test_sms_endpoint_returns_twiml(client):
    response = client.post("/sms", data={"From": "+17135550199", "Body": "I need food near 77004"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert "<Message>" in response.text
    assert "Pantry A" in response.text
    # The phone number is the session id.
    assert "+17135550199" in main.SESSIONS


def test_api_requests_counts_and_privacy(client):
    client.post("/sms", data={"From": "+17135550199", "Body": "I need food near 77004"})
    ask(client, "I need food", session_id="secret-session", channel="kiosk")
    ask(client, "hello", session_id="secret-session", channel="kiosk")  # no need: not logged
    data = client.get("/api/requests").json()
    assert data["counts"] == {"food": 2}
    assert data["unmet_counts"] == {}
    assert [r["id"] for r in data["requests"]] == [1, 2]
    first = data["requests"][0]
    assert first["channel"] == "sms" and first["matched"] is True
    assert first["place"] == "Pantry A"
    assert first["text"] == "I need food near 77004"
    assert set(first) == {"id", "time", "channel", "need", "language", "lat", "lng",
                          "matched", "place", "text"}
    raw = client.get("/api/requests").text
    assert "+17135550199" not in raw
    assert "secret-session" not in raw
    assert "session" not in raw and "From" not in raw


def test_log_text_is_truncated(client):
    ask(client, "I need food " + "x" * 300, channel="kiosk")
    row = client.get("/api/requests").json()["requests"][-1]
    assert len(row["text"]) == 140


def test_help_intent(client):
    ask(client, "I need food near 77004")
    ask(client, "2")
    data = ask(client, "HELP")
    assert data["intent"] == "help"
    assert "211" in data["reply"]
    assert "713-555-0002" in data["reply"]  # the selected place's phone


def test_help_in_spanish_without_options(client):
    data = ask(client, "ayuda", lang="es")
    assert data["intent"] == "help"
    assert "Llame al 211" in data["reply"]


def test_followup_uses_session_options(client, calls):
    ask(client, "I need food near 77004")
    data = ask(client, "is it far?")
    assert data["intent"] == "followup"
    assert len(calls) == 1
    assert data["selected"] == 1


def test_other_intent(client):
    data = ask(client, "hello there")
    assert data["intent"] == "other"
    assert "HelpLine Houston" in data["reply"]


def test_session_expires(client, calls, monkeypatch):
    clock = {"now": 1_000_000.0}
    monkeypatch.setattr(main.time, "time", lambda: clock["now"])
    ask(client, "I need food near 77004")
    assert "s1" in main.SESSIONS

    clock["now"] += 31 * 60
    data = ask(client, "2", session_id="someone-else")
    assert "s1" not in main.SESSIONS  # cleaned up on the next call
    data = ask(client, "2")
    assert data["options"] == [] and data["selected"] == 0


def test_session_kept_within_30_minutes(client, monkeypatch):
    clock = {"now": 1_000_000.0}
    monkeypatch.setattr(main.time, "time", lambda: clock["now"])
    ask(client, "I need food near 77004")
    clock["now"] += 29 * 60
    assert ask(client, "2")["selected"] == 2


def test_history_keeps_last_six(client):
    for _ in range(5):
        ask(client, "hello")
    assert len(main.SESSIONS["s1"]["history"]) == 6


def test_error_gives_safe_reply(client, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(main.brain, "understand", broken)
    data = ask(client, "I need food")
    assert data["reply"] == main.ERROR_REPLY
    assert data["options"] == [] and data["selected"] == 0
    response = client.post("/sms", data={"From": "+1", "Body": "hi"})
    assert "211" in response.text and "<Message>" in response.text


def test_tts_success_and_unavailable(client, monkeypatch):
    seen = {}

    def fake_tts(text, lang):
        seen["args"] = (text, lang)
        return b"ID3fake"

    monkeypatch.setattr(main.voice, "tts", fake_tts)
    response = client.post("/tts", json={"text": "Hola", "lang": "es"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.content == b"ID3fake"
    assert seen["args"] == ("Hola", "es")

    monkeypatch.setattr(main.voice, "tts", lambda text, lang: None)
    response = client.post("/tts", json={"text": "Hola", "lang": "es"})
    assert response.status_code == 503
    assert response.json() == {"error": "tts unavailable"}


def test_stt_success_and_unavailable(client, monkeypatch):
    seen = {}

    def fake_stt(audio, filename, content_type, lang):
        seen["args"] = (audio, filename, content_type, lang)
        return {"text": "necesito comida", "language": "es"}

    monkeypatch.setattr(main.voice, "stt", fake_stt)
    files = {"file": ("clip.webm", b"audio-bytes", "audio/webm")}
    response = client.post("/stt", files=files, data={"lang": "es"})
    assert response.status_code == 200
    assert response.json() == {"text": "necesito comida", "language": "es"}
    assert seen["args"] == (b"audio-bytes", "clip.webm", "audio/webm", "es")

    monkeypatch.setattr(main.voice, "stt", lambda *args: None)
    response = client.post("/stt", files=files)
    assert response.status_code == 503
    assert response.json() == {"error": "stt unavailable"}


def test_pages_route_to_static_files(client):
    assert main.STATIC_DIR == main.APP_DIR / "static"
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "html" in response.text.lower()
