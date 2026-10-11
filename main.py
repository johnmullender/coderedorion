"""HelpLine Houston web app: wires the brain, matching, location, db and voice together.

Routes:
- POST /ask           JSON question from the kiosk (or any client) -> JSON answer
- POST /sms           Twilio text message webhook -> TwiML reply
- POST /tts, /stt     ElevenLabs speech for the kiosk (key stays on the server)
- GET  /api/requests  anonymous request log for the dashboard
- GET  /kiosk, /dashboard, /   pages and a health check

Everything is synchronous (plain `def`), as the contract asks.
"""

import logging
import pathlib
import time
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel
from twilio.twiml.messaging_response import MessagingResponse

import brain
import db
import location
import matching
import voice

log = logging.getLogger("helpline")

HOUSTON = ZoneInfo("America/Chicago")
APP_DIR = pathlib.Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"

SESSION_TIMEOUT_SECONDS = 30 * 60   # forget a conversation after 30 idle minutes
HISTORY_LIMIT = 6                   # keep the last 6 history items (user + bot)
LOG_TEXT_LIMIT = 140                # how much of the user's text goes in the request log
REQUEST_LOG_LIMIT = 500             # how many requests /api/requests shows

ERROR_REPLY = "Sorry, something went wrong. Call 211 for help."
OPTION_KEYS = ["id", "name", "address", "distance_mi", "open_now", "closes_at", "opens_at", "phone"]

# ---------------------------------------------------------------------------
# Short fixed messages. English and Spanish; other languages use English.
# ---------------------------------------------------------------------------

MESSAGES = {
    "en": {
        "ask_need": "What do you need? I can help find food, shelter, showers, a clinic, a library, a cooling center, or legal help.",
        "ask_location": "What zip code or street are you near?",
        "help": "Call 211 (free, 24/7) to talk to a person.",
        "help_place": "{name}: {phone}.",
        "bad_number": "Please reply with a number from 1 to {count}.",
        "no_options": "I don't have any places for you yet. What do you need? For example: food, shelter, or a clinic.",
        "intro": "Hi, this is HelpLine Houston. Tell me what you need (food, shelter, showers, a clinic, a library, a cooling center, or legal help) and where you are, and I'll find a free place open now.",
    },
    "es": {
        "ask_need": "¿Qué necesita? Puedo ayudar a encontrar comida, refugio, duchas, una clínica, una biblioteca, un centro de enfriamiento o ayuda legal.",
        "ask_location": "¿Cerca de qué código postal o calle está?",
        "help": "Llame al 211 (gratis, 24/7) para hablar con una persona.",
        "help_place": "{name}: {phone}.",
        "bad_number": "Por favor responda con un número del 1 al {count}.",
        "no_options": "Todavía no tengo lugares para usted. ¿Qué necesita? Por ejemplo: comida, refugio o una clínica.",
        "intro": "Hola, esta es HelpLine Houston. Dígame qué necesita (comida, refugio, duchas, una clínica, una biblioteca, un centro de enfriamiento o ayuda legal) y dónde está, y le busco un lugar gratis abierto ahora.",
    },
}


def message(language, key, **values):
    """Look up a fixed message in the person's language (English if we don't have it)."""
    table = MESSAGES.get(language, MESSAGES["en"])
    return table[key].format(**values)


# ---------------------------------------------------------------------------
# Session memory: one dict per session_id, kept in memory only.
# ---------------------------------------------------------------------------

SESSIONS = {}


def new_session():
    """A fresh, empty conversation."""
    return {
        "options": [],          # the match dicts we last offered
        "need": None,           # the last need searched (or waiting for a location)
        "lat": None,
        "lng": None,
        "language": "en",
        "history": [],          # [{"role": "user"|"bot", "text": ...}], last 6
        "selected": 0,          # which option (1-based) the last reply was about
        "awaiting_location": False,  # True after we asked "what zip are you near?"
        "last_seen": time.time(),
    }


def clean_expired_sessions():
    """Forget every conversation that has been idle for more than 30 minutes."""
    now = time.time()
    for session_id in list(SESSIONS):
        if now - SESSIONS[session_id]["last_seen"] > SESSION_TIMEOUT_SECONDS:
            del SESSIONS[session_id]


def get_session(session_id):
    """Return the session for this id, creating it if needed."""
    clean_expired_sessions()
    session = SESSIONS.get(session_id)
    if session is None:
        session = new_session()
        SESSIONS[session_id] = session
    session["last_seen"] = time.time()
    return session


def remember_turn(session, user_text, reply):
    """Add the user's message and our reply to the history, keeping the last 6."""
    session["history"].append({"role": "user", "text": user_text})
    session["history"].append({"role": "bot", "text": reply})
    session["history"] = session["history"][-HISTORY_LIMIT:]


# ---------------------------------------------------------------------------
# Helpers for building the answer
# ---------------------------------------------------------------------------

def public_options(options):
    """Turn match dicts into the small option list the /ask JSON returns."""
    result = []
    for number, match in enumerate(options, start=1):
        item = {"n": number}
        for key in OPTION_KEYS:
            item[key] = match.get(key)
        result.append(item)
    return result


def chosen_first(options, number):
    """Put option `number` (1-based) first, then the others in their usual order."""
    chosen = options[number - 1]
    others = [m for i, m in enumerate(options, start=1) if i != number]
    return [chosen] + others


def make_result(session, reply, intent, need=None, unmet=False, ask_location=False):
    """Build the /ask JSON shape from the session's current options."""
    return {
        "reply": reply,
        "language": session["language"],
        "need": need,
        "intent": intent,
        "options": public_options(session["options"]),
        "selected": session["selected"],
        "unmet": unmet,
        "ask_location": ask_location,
    }


def find_location(channel, understood, text, lat, lng, session):
    """Work out where the person is. Returns (lat, lng) or (None, None)."""
    # 1. The client sent coordinates (kiosk, or a phone app).
    if lat is not None and lng is not None:
        return lat, lng
    # 2. The kiosk sits at a fixed spot.
    if channel == "kiosk":
        return location.STATION_LAT, location.STATION_LNG
    # 3. SMS: look for a zip / landmark / street in the message.
    #    When the brain found place words we look those up (zip, landmark or
    #    street). Otherwise we only check the whole message for a zip or a
    #    landmark: sending "I need food" to the street geocoder could match
    #    some random spot in Houston.
    location_text = understood.get("location_text")
    if location_text:
        place = location.resolve_location(location_text)
    else:
        place = location.find_zip(text) or location.find_landmark(text)
    if place is not None:
        return place["lat"], place["lng"]
    # 4. SMS: the last place we knew for this person.
    if session["lat"] is not None and session["lng"] is not None:
        return session["lat"], session["lng"]
    return None, None


def log_request(channel, need, language, lat, lng, options, text):
    """Save an anonymous record of the search for the dashboard (no phone, no session id)."""
    db.log_request({
        "time": datetime.now(HOUSTON).replace(microsecond=0).isoformat(),
        "channel": channel,
        "need": need,
        "language": language,
        "lat": lat,
        "lng": lng,
        "matched": bool(options),
        "place": options[0].get("name") if options else None,
        "text": (text or "")[:LOG_TEXT_LIMIT],
    })


# ---------------------------------------------------------------------------
# One handler per intent
# ---------------------------------------------------------------------------

def handle_next_option(session, understood, text, channel):
    """The person picked option 2, 3, ... from the list we already gave."""
    language = session["language"]
    options = session["options"]
    number = understood.get("option_number")
    # No list yet: ask what they need.
    if not options:
        return make_result(session, message(language, "no_options"), "next_option")
    # Number out of range: say which numbers work.
    if not isinstance(number, int) or number < 1 or number > len(options):
        reply = message(language, "bad_number", count=len(options))
        return make_result(session, reply, "next_option", need=session["need"])
    # Answer about the chosen place (it goes first, so it is the recommended one).
    reply = brain.answer(text, language, session["need"], chosen_first(options, number),
                         channel, history=session["history"])
    session["selected"] = number
    return make_result(session, reply, "next_option", need=session["need"])


def handle_followup(session, text, channel):
    """A question about the places we already gave ("is it far?")."""
    selected = session["selected"] or 1
    matches = chosen_first(session["options"], selected)
    reply = brain.answer(text, session["language"], session["need"], matches,
                         channel, history=session["history"])
    session["selected"] = selected
    return make_result(session, reply, "followup", need=session["need"])


def handle_help(session):
    """The person wants a human: give 211 and the selected place's phone."""
    language = session["language"]
    reply = message(language, "help")
    selected = session["selected"]
    if selected and selected <= len(session["options"]):
        place = session["options"][selected - 1]
        if place.get("phone"):
            reply += " " + message(language, "help_place", name=place.get("name"), phone=place["phone"])
    return make_result(session, reply, "help", need=session["need"])


def handle_find(session, understood, text, channel, lat, lng):
    """Search for open places for the person's need near where they are."""
    language = session["language"]
    need = understood.get("need") or session["need"]

    # Step 1: we need to know what they need.
    if not need:
        return make_result(session, message(language, "ask_need"), "find")
    session["need"] = need

    # Step 2: we need to know where they are.
    lat, lng = find_location(channel, understood, text, lat, lng, session)
    if lat is None or lng is None:
        # Remember the need so a reply with just a zip finishes the search.
        session["awaiting_location"] = True
        session["options"] = []
        session["selected"] = 0
        return make_result(session, message(language, "ask_location"), "find",
                           need=need, ask_location=True)
    session["awaiting_location"] = False
    session["lat"], session["lng"] = lat, lng

    # Step 3: search, then let the brain write the reply.
    found = matching.find_matches(need, lat, lng, resources=db.get_resources())
    options = found.get("options") or []
    unmet_next = found.get("next_open") if not options else None
    reply = brain.answer(text, language, need, options, channel,
                         unmet_next=unmet_next, history=session["history"])

    # Step 4: save and log.
    session["options"] = options
    session["selected"] = 1 if options else 0
    log_request(channel, need, language, lat, lng, options, text)
    return make_result(session, reply, "find", need=need, unmet=not options)


def handle_other(session):
    """Small talk or something we don't understand: say what the line can do."""
    return make_result(session, message(session["language"], "intro"), "other")


# ---------------------------------------------------------------------------
# The core: used by both /ask and /sms
# ---------------------------------------------------------------------------

def choose_intent(understood, session):
    """Decide which handler to use, using the session to fill in gaps."""
    intent = understood.get("intent")
    need = understood.get("need")
    if intent in ("next_option", "help"):
        return intent
    # A new need always means a new search.
    if need and need != session["need"]:
        return "find"
    if intent == "followup":
        return "followup" if session["options"] else "find"
    # We asked for a location (or already know the need) and this message
    # has one, e.g. just "77004" or "near downtown": search again there.
    if session["need"] and understood.get("location_text"):
        return "find"
    if intent == "find" or need:
        return "find"
    return "other"


def _handle_ask(channel, session_id, text, lang, lat, lng):
    """The real work behind handle_ask (without the safety net)."""
    # Step 1: find or start this person's conversation.
    session = get_session(session_id)

    # Step 2: let the brain read the message.
    understood = brain.understand(text, session["history"], lang_hint=lang)

    # Step 3: pick the language. When we are waiting for a zip, a bare "77004"
    # says nothing about language, so keep the one we were using.
    if lang:
        session["language"] = lang
    elif not (session["awaiting_location"] and not understood.get("need")):
        session["language"] = understood.get("language") or "en"

    # Step 4: answer by intent.
    intent = choose_intent(understood, session)
    if intent == "next_option":
        result = handle_next_option(session, understood, text, channel)
    elif intent == "followup":
        result = handle_followup(session, text, channel)
    elif intent == "help":
        result = handle_help(session)
    elif intent == "find":
        result = handle_find(session, understood, text, channel, lat, lng)
    else:
        result = handle_other(session)

    # Step 5: remember this turn.
    remember_turn(session, text, result["reply"])
    return result


def handle_ask(channel, session_id, text, lang=None, lat=None, lng=None):
    """Answer one message. Never raises: on any error it returns a safe 211 reply."""
    try:
        return _handle_ask(channel, session_id, text or "", lang, lat, lng)
    except Exception:
        log.exception("handle_ask failed")
        return {
            "reply": ERROR_REPLY,
            "language": lang or "en",
            "need": None,
            "intent": "other",
            "options": [],
            "selected": 0,
            "unmet": False,
            "ask_location": False,
        }


# ---------------------------------------------------------------------------
# The web app
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(_app):
    """Runs once when the server starts: make sure the database tables exist."""
    db.init_db()
    yield


app = FastAPI(title="HelpLine Houston", lifespan=lifespan)


class AskBody(BaseModel):
    channel: str = "kiosk"
    session_id: str
    text: str = ""
    lang: str | None = None
    lat: float | None = None
    lng: float | None = None


class TtsBody(BaseModel):
    text: str
    lang: str = "en"


@app.get("/")
def health():
    return {"ok": True, "service": "HelpLine Houston"}


@app.post("/ask")
def ask(body: AskBody):
    return handle_ask(body.channel, body.session_id, body.text, body.lang, body.lat, body.lng)


@app.post("/sms")
def sms(From: str = Form(""), Body: str = Form("")):
    # Same logic as /ask, with the phone number as the session id (never logged).
    result = handle_ask("sms", From, Body)
    twiml = MessagingResponse()
    twiml.message(result["reply"])
    return Response(content=str(twiml), media_type="application/xml")


@app.post("/tts")
def tts(body: TtsBody):
    audio = voice.tts(body.text, body.lang)
    if audio is None:
        return JSONResponse({"error": "tts unavailable"}, status_code=503)
    return Response(content=audio, media_type="audio/mpeg")


@app.post("/stt")
def stt(file: UploadFile = File(...), lang: str | None = Form(None)):
    audio = file.file.read()
    result = voice.stt(audio, file.filename, file.content_type, lang)
    if result is None:
        return JSONResponse({"error": "stt unavailable"}, status_code=503)
    return result


@app.get("/api/requests")
def api_requests():
    rows = db.recent_requests(REQUEST_LOG_LIMIT)
    counts = {}
    unmet_counts = {}
    for row in rows:
        need = row.get("need")
        if not need:
            continue
        counts[need] = counts.get(need, 0) + 1
        if not row.get("matched"):
            unmet_counts[need] = unmet_counts.get(need, 0) + 1
    return {"requests": rows, "counts": counts, "unmet_counts": unmet_counts}


@app.get("/kiosk")
def kiosk_page():
    return FileResponse(STATIC_DIR / "kiosk.html")


@app.get("/dashboard")
def dashboard_page():
    return FileResponse(STATIC_DIR / "dashboard.html")
