"""HelpLine Houston web app: wires the brain, matching, location, db, eligibility, transit and voice together.

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
import re
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
import eligibility
import google_transit
import location
import matching
import transit
import voice

log = logging.getLogger("helpline")

HOUSTON = ZoneInfo("America/Chicago")
APP_DIR = pathlib.Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"

SESSION_TIMEOUT_SECONDS = 30 * 60   # forget a conversation after 30 idle minutes
HISTORY_LIMIT = 6                   # keep the last 6 history items (user + bot)
LOG_TEXT_LIMIT = 140                # how much of the user's text goes in the request log
REQUEST_LOG_LIMIT = 500             # how many requests /api/requests shows
MATCH_LIMIT = 8                     # open places kept for the eligibility questions
OPTIONS_SHOWN = 3                   # how many of the eligible places we offer
SMS_LIMIT = 300                     # the longest SMS reply we send

ERROR_REPLY = "Sorry, something went wrong. Call 211 for help."
OPTION_KEYS = ["id", "name", "address", "distance_mi", "open_now", "closes_at", "opens_at", "phone"]

# A message that is only one of these words asks for the bus directions.
BUS_WORDS = {"bus", "autobús", "autobus", "xe buýt", "xe buyt", "公交", "حافلة"}

ZIP_PATTERN = re.compile(r"(?<!\d)(\d{5})(?!\d)")

# Google Maps transit directions, sent on SMS when no bus route is found.
MAPS_TRANSIT_URL = ("https://www.google.com/maps/dir/?api=1&origin={from_lat:.6f},{from_lng:.6f}"
                    "&destination={to_lat:.6f},{to_lng:.6f}&travelmode=transit")

# ---------------------------------------------------------------------------
# Short fixed messages, one set per language.
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
        "none_fit": "None of the open places fit your answers.",
        "eligible": "Based on your answers, you're eligible for:",
        "nothing_open": "Nothing nearby is open right now.",
        "no_bus": "No direct bus or transfer found to {name} ({dist}).",
        "bus_unavailable": "Bus times aren't available right now. {name} is {dist}.",
        "to_place": "To {name}: {text}",
        "map_link": "Google Maps transit route: {link}",
    },
    "es": {
        "ask_need": "¿Qué necesita? Puedo ayudar a encontrar comida, refugio, duchas, una clínica, una biblioteca, un centro de enfriamiento o ayuda legal.",
        "ask_location": "¿Cerca de qué código postal o calle está?",
        "help": "Llame al 211 (gratis, 24/7) para hablar con una persona.",
        "help_place": "{name}: {phone}.",
        "bad_number": "Por favor responda con un número del 1 al {count}.",
        "no_options": "Todavía no tengo lugares para usted. ¿Qué necesita? Por ejemplo: comida, refugio o una clínica.",
        "intro": "Hola, esta es HelpLine Houston. Dígame qué necesita (comida, refugio, duchas, una clínica, una biblioteca, un centro de enfriamiento o ayuda legal) y dónde está, y le busco un lugar gratis abierto ahora.",
        "none_fit": "Ninguno de los lugares abiertos le corresponde según sus respuestas.",
        "eligible": "Según sus respuestas, usted califica para:",
        "nothing_open": "Nada cercano está abierto ahora.",
        "no_bus": "No encontré autobús directo ni con transbordo a {name} ({dist}).",
        "bus_unavailable": "Los horarios del autobús no están disponibles ahora. {name} está {dist}.",
        "to_place": "Para {name}: {text}",
        "map_link": "Ruta en Google Maps: {link}",
    },
    "vi": {
        "ask_need": "Bạn cần gì? Tôi có thể giúp tìm thức ăn, chỗ ngủ, chỗ tắm, phòng khám, thư viện, nơi tránh nóng hoặc trợ giúp pháp lý.",
        "ask_location": "Bạn đang ở gần mã bưu điện hoặc con đường nào?",
        "help": "Gọi 211 (miễn phí, 24/7) để nói chuyện với một người.",
        "help_place": "{name}: {phone}.",
        "bad_number": "Vui lòng trả lời bằng một số từ 1 đến {count}.",
        "no_options": "Tôi chưa có địa điểm nào cho bạn. Bạn cần gì? Ví dụ: thức ăn, chỗ ngủ hoặc phòng khám.",
        "intro": "Xin chào, đây là HelpLine Houston. Hãy cho tôi biết bạn cần gì (thức ăn, chỗ ngủ, chỗ tắm, phòng khám, thư viện, nơi tránh nóng hoặc trợ giúp pháp lý) và bạn đang ở đâu, tôi sẽ tìm một nơi miễn phí đang mở cửa.",
        "none_fit": "Không có địa điểm nào đang mở phù hợp với câu trả lời của bạn.",
        "eligible": "Dựa trên câu trả lời của bạn, bạn đủ điều kiện cho:",
        "nothing_open": "Hiện không có nơi nào gần đây đang mở cửa.",
        "no_bus": "Không tìm thấy xe buýt trực tiếp hoặc có đổi tuyến đến {name} ({dist}).",
        "bus_unavailable": "Hiện chưa có giờ xe buýt. {name} {dist}.",
        "to_place": "Đến {name}: {text}",
        "map_link": "Đường đi trên Google Maps: {link}",
    },
    "zh": {
        "ask_need": "您需要什么？我可以帮您找食物、住宿、洗浴、诊所、图书馆、避暑中心或法律援助。",
        "ask_location": "您在哪个邮编或哪条街附近？",
        "help": "请拨打211（免费，全天24小时）与真人通话。",
        "help_place": "{name}：{phone}。",
        "bad_number": "请回复1到{count}之间的数字。",
        "no_options": "我还没有为您找到地点。您需要什么？例如：食物、住宿或诊所。",
        "intro": "您好，这里是HelpLine Houston。请告诉我您需要什么（食物、住宿、洗浴、诊所、图书馆、避暑中心或法律援助）以及您在哪里，我会为您找一个现在开放的免费地点。",
        "none_fit": "开放的地点都不符合您的情况。",
        "eligible": "根据您的回答，您符合以下地点的条件：",
        "nothing_open": "附近现在没有开放的地点。",
        "no_bus": "找不到到{name}的直达或换乘公交（{dist}）。",
        "bus_unavailable": "现在无法获取公交时间。{name}{dist}。",
        "to_place": "前往{name}：{text}",
        "map_link": "谷歌地图公交路线：{link}",
    },
    "ar": {
        "ask_need": "ماذا تحتاج؟ يمكنني المساعدة في إيجاد الطعام أو المأوى أو الاستحمام أو عيادة أو مكتبة أو مركز تبريد أو مساعدة قانونية.",
        "ask_location": "بالقرب من أي رمز بريدي أو شارع أنت؟",
        "help": "اتصل بالرقم 211 (مجاني، على مدار الساعة) للتحدث مع شخص.",
        "help_place": "{name}: {phone}.",
        "bad_number": "يرجى الرد برقم من 1 إلى {count}.",
        "no_options": "ليس لدي أماكن لك بعد. ماذا تحتاج؟ مثلا: طعام أو مأوى أو عيادة.",
        "intro": "مرحبا، هذا HelpLine Houston. أخبرني ماذا تحتاج (طعام أو مأوى أو استحمام أو عيادة أو مكتبة أو مركز تبريد أو مساعدة قانونية) وأين أنت، وسأجد لك مكانا مجانيا مفتوحا الآن.",
        "none_fit": "لا يوجد مكان مفتوح يناسب إجاباتك.",
        "eligible": "بناءً على إجاباتك، أنت مؤهل لـ:",
        "nothing_open": "لا يوجد شيء قريب مفتوح الآن.",
        "no_bus": "لم أجد حافلة مباشرة أو مع تبديل إلى {name} ({dist}).",
        "bus_unavailable": "أوقات الحافلات غير متوفرة الآن. {name} {dist}.",
        "to_place": "إلى {name}: {text}",
        "map_link": "مسار غوغل ماب للحافلات: {link}",
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
        "answers": {},          # eligibility answers for the current search: {question_key: True|False|None}
        "asked": 0,             # how many eligibility questions this search has asked
        "awaiting_answer": None,     # the question key we just asked, or None
        "pending": [],          # the open places this search is still deciding between
        "search_text": "",      # the message that started this search
        "zip": None,            # 5-digit zip of the person's location (typed, or from zips.json), if known
        "unknown": [],          # nearest places in the need with no hours listed (fallback options)
        "next_open": None,      # the place in the need that opens soonest, from the search
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
        if match.get("hours_unknown"):
            item["hours_unknown"] = True
        result.append(item)
    return result


def chosen_first(options, number):
    """Put option `number` (1-based) first, then the others in their usual order."""
    chosen = options[number - 1]
    others = [m for i, m in enumerate(options, start=1) if i != number]
    return [chosen] + others


def make_result(session, reply, intent, need=None, unmet=False, ask_location=False,
                asking=None, transit=None):
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
        "asking": asking,
        "transit": transit,
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


def zip_for_coords(lat, lng):
    """The zip code whose zips.json entry has these exact coordinates, or None."""
    for code, entry in location.load_zip_table().items():
        if entry.get("lat") == lat and entry.get("lng") == lng:
            return code
    return None


def zip_from(understood, text, lat, lng):
    """The zip for eligibility: a 5-digit zip the person wrote, else the zip of the resolved location."""
    source = understood.get("location_text") or text or ""
    match = ZIP_PATTERN.search(source)
    if match:
        return match.group(1)
    return zip_for_coords(lat, lng)


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


def bus_for(session, place):
    """The bus route from where the person is to `place`, or None (no coordinates, no ride, or an error).

    Google Maps goes first when a key is set. When it gives no bus route, the METRO schedule is the backup.
    """
    origin_lat, origin_lng = session["lat"], session["lng"]
    to_lat, to_lng = place.get("lat"), place.get("lng")
    if None in (origin_lat, origin_lng, to_lat, to_lng):
        return None
    try:
        if google_transit.enabled():
            found = google_transit.directions(origin_lat, origin_lng, to_lat, to_lng,
                                              language=session["language"])
            if found is not None:
                return found
        return transit.directions(origin_lat, origin_lng, to_lat, to_lng)
    except Exception:
        log.exception("bus directions failed")
        return None



def maps_link(session, place):
    """The Google Maps transit link from the person to the place, or None when either point is unknown."""
    if session["lat"] is None or place.get("lat") is None:
        return None
    return MAPS_TRANSIT_URL.format(from_lat=session["lat"], from_lng=session["lng"],
                                   to_lat=place["lat"], to_lng=place["lng"])


def with_link(text, link_text):
    """The SMS text followed by a link. The text is shortened first so the whole reply fits SMS_LIMIT."""
    room = SMS_LIMIT - len(link_text) - 1
    if len(text) > room:
        text = text[:max(0, room - 3)] + "..."
    return text + " " + link_text


def compose(base, extras):
    """The base reply with each extra sentence after it (empty extras are skipped)."""
    return " ".join([base] + [extra for extra in extras if extra])


def more_hint(session, channel):
    """The "more options" hint. It appears only when another option is listed after the one the reply is about."""
    shown = session["selected"]
    if not 1 <= shown < len(session["options"]):
        return ""
    return brain.more_options_hint(session["language"], channel, shown + 1)


def with_offers_and_hint(language, base, place, hint, channel):
    """The main sentence, then the Offers line (when `place` lists offers), then the hint.

    On SMS the reply stays within SMS_LIMIT: the offers line goes first, then the main sentence is shortened.
    The hint always stays last.
    """
    offers = brain.offers_line(place, language) if place else None
    text = compose(base, [offers, hint])
    if channel != "sms" or len(text) <= SMS_LIMIT:
        return text
    text = compose(base, [hint])
    if len(text) <= SMS_LIMIT:
        return text
    room = SMS_LIMIT - len(hint) - 1 if hint else SMS_LIMIT
    return compose(base[:max(0, room - 3)] + "...", [hint])


def add_extras(session, base, place, channel):
    """Finish a reply about `place`: the main sentence, then its Offers line, then the more-options hint.

    No address, no phone, no eligibility notes and no bus line. Bus directions come only when the person
    asks for them (the directions intent). Returns the reply text.
    """
    return with_offers_and_hint(session["language"], base, place, more_hint(session, channel), channel)


def clip_for_channel(text, channel):
    """SMS replies never go past SMS_LIMIT characters."""
    if channel == "sms" and len(text) > SMS_LIMIT:
        return text[:SMS_LIMIT - 3] + "..."
    return text


def _miles_value(session, place):
    """The distance to the place in miles (a float)."""
    if place.get("distance_mi") is not None:
        return float(place["distance_mi"])
    if session["lat"] is not None and place.get("lat") is not None:
        return matching.haversine_miles(session["lat"], session["lng"], place["lat"], place["lng"])
    return 0.0


# The distance wording now lives in brain.py (the next-open sentence uses it too); this name stays for handle_directions.
distance_phrase = brain.distance_phrase


def bus_data_available():
    """False when there is no bus data at all: no Google key, no database and no local transit file."""
    return google_transit.enabled() or db.enabled() or transit.LOCAL_PATH.exists()


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
    reply = add_extras(session, reply, options[number - 1], channel)
    return make_result(session, reply, "next_option", need=session["need"])


def handle_directions(session, channel):
    """How to get to the selected place by bus.

    The reply names the place. With no bus route, it says so, and on SMS it ends with a Google Maps
    transit link that plans a real route. With no bus data at all, it says bus times aren't there.
    """
    language = session["language"]
    options = session["options"]
    if not options:
        return make_result(session, message(language, "no_options"), "directions", need=session["need"])
    number = session["selected"] if 1 <= session["selected"] <= len(options) else 1
    session["selected"] = number
    place = options[number - 1]
    name = place.get("name") or ""
    dist = distance_phrase(language, _miles_value(session, place))
    found = bus_for(session, place)
    if found is not None:
        reply = message(language, "to_place", name=name, text=transit.summary(found, language, channel))
        return make_result(session, clip_for_channel(reply, channel), "directions", need=session["need"],
                           transit=found)

    if not bus_data_available():
        reply = message(language, "bus_unavailable", name=name, dist=dist)
    else:
        # No route found: don't name stops that may not connect; the SMS map link plans a real route.
        reply = message(language, "no_bus", name=name, dist=dist)
    link = maps_link(session, place) if channel == "sms" else None
    if link:
        reply = with_link(reply, message(language, "map_link", link=link))
    reply = clip_for_channel(reply, channel)
    return make_result(session, reply, "directions", need=session["need"], transit=None)


def handle_followup(session, text, channel):
    """A question about the places we already gave ("is it far?")."""
    selected = session["selected"] or 1
    matches = chosen_first(session["options"], selected)
    reply = brain.answer(text, session["language"], session["need"], matches,
                         channel, history=session["history"])
    session["selected"] = selected
    reply = with_offers_and_hint(session["language"], reply, None, more_hint(session, channel), channel)
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


def search_step(session, channel):
    """Ask the next eligibility question, or give the final answer for the search.

    The open places still in play are the pending ones that no answer has ruled out. When fewer
    than OPTIONS_SHOWN open places remain, the nearest places in the need with no listed hours
    fill the gap (marked hours_unknown, never "open").
    """
    language = session["language"]
    need = session["need"]
    answers = session["answers"]
    zip_code = session["zip"]
    places = eligibility.filter_places(session["pending"], answers, zip_code)

    question = eligibility.next_question(places, answers, zip_code, session["asked"])
    if question is not None:
        key, rule = question
        session["asked"] += 1
        session["awaiting_answer"] = key
        session["options"] = places[:OPTIONS_SHOWN]
        session["selected"] = 1 if places else 0
        reply = eligibility.question_text(key, rule, language)
        return make_result(session, reply, "find", need=need, asking=key)

    session["awaiting_answer"] = None
    fallback = eligibility.filter_places(session["unknown"], answers, zip_code)

    if not session["pending"]:
        # Nothing open for this need: say so, with the place that opens next, then the no-hours places.
        return nothing_open_step(session, fallback, channel)

    options = (places + fallback)[:OPTIONS_SHOWN]
    session["options"] = options

    if not options:
        # Open places were found, but the answers ruled them all out.
        session["selected"] = 0
        next_place = matching.next_open_for(need, session["lat"], session["lng"],
                                            resources=db.get_resources())
        fit = message(language, "none_fit")
        if next_place is not None:
            reply = with_offers_and_hint(language, fit + " " + brain.next_open_text(language, next_place),
                                         next_place, "", channel)
        else:
            reply = fit + " " + message(language, "help")
        reply = clip_for_channel(reply, channel)
        return make_result(session, reply, "find", need=need, unmet=True)

    ruled_out = bool(session["pending"]) and not places
    open_offered = any(not match.get("hours_unknown") for match in options)
    session["selected"] = 1
    reply = brain.answer(session["search_text"], language, need, options, channel,
                         history=session["history"])
    if ruled_out:
        reply = clip_for_channel(message(language, "none_fit") + " " + reply, channel)
    elif options[0] in places and passed_a_question(options[0], answers):
        # We asked questions and this place fits: say so, so the questions make sense.
        reply = clip_for_channel(message(language, "eligible") + " " + reply, channel)
    reply = add_extras(session, reply, options[0], channel)
    return make_result(session, reply, "find", need=need, unmet=not open_offered)


def passed_a_question(place, answers):
    """True when the person answered yes to at least one question this place asks."""
    return any(answers.get(eligibility.question_key(rule)) is True
               for rule in eligibility.rules_to_check(place) if eligibility.question_key(rule))


def nothing_open_step(session, fallback, channel):
    """No place is open for this need: "Nothing nearby is open right now", then the place that opens next.

    With a next place, it is option 1: one sentence for it (name, opening time, distance), its Offers line,
    and the hint to see the nearest places with no hours listed (options 2 and 3). Without a next place, the
    no-hours places are described as before. With neither, the reply points to 211.
    """
    language = session["language"]
    need = session["need"]
    next_open = session["next_open"]

    if next_open is None:
        options = fallback[:OPTIONS_SHOWN]
        session["options"] = options
        session["selected"] = 1 if options else 0
        if not options:
            reply = brain.answer(session["search_text"], language, need, [], channel,
                                 unmet_next=None, history=session["history"])
            return make_result(session, clip_for_channel(reply, channel), "find", need=need, unmet=True)
        rest = brain.answer(session["search_text"], language, need, options, channel,
                            history=session["history"])
        reply = compose(message(language, "nothing_open"), [rest])
        if channel == "sms" and len(reply) > SMS_LIMIT:
            # The places with no hours listed do not fit: keep the lead, and drop them.
            reply = message(language, "nothing_open")
            session["options"] = []
            session["selected"] = 0
        else:
            reply = add_extras(session, reply, options[0], channel)
        return make_result(session, clip_for_channel(reply, channel), "find", need=need, unmet=True)

    options = ([next_open] + fallback)[:OPTIONS_SHOWN]
    session["options"] = options
    session["selected"] = 1
    lead = message(language, "nothing_open") + " " + brain.next_open_text(language, next_open)
    reply = add_extras(session, lead, next_open, channel)
    return make_result(session, clip_for_channel(reply, channel), "find", need=need, unmet=True)


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
        session["awaiting_answer"] = None
        session["options"] = []
        session["selected"] = 0
        return make_result(session, message(language, "ask_location"), "find",
                           need=need, ask_location=True)
    session["awaiting_location"] = False
    session["lat"], session["lng"] = lat, lng

    # Step 3: find the open places (the top MATCH_LIMIT) and the no-hours fallback; log the search once.
    resources = db.get_resources()
    found = matching.find_matches(need, lat, lng, resources=resources, limit=MATCH_LIMIT)
    candidates = found.get("options") or []
    log_request(channel, need, language, lat, lng, candidates[:OPTIONS_SHOWN], text)

    # Start a fresh search: no answers carried over from an earlier need.
    session.update(awaiting_answer=None, answers={}, asked=0, pending=candidates,
                   unknown=matching.find_unknown_hours(need, lat, lng, resources=resources, limit=MATCH_LIMIT),
                   next_open=found.get("next_open"), search_text=text,
                   zip=zip_from(understood, text, lat, lng))

    # Step 4: ask eligibility questions, or answer right away.
    return search_step(session, channel)


def handle_other(session):
    """Small talk or something we don't understand: say what the line can do."""
    return make_result(session, message(session["language"], "intro"), "other")


# ---------------------------------------------------------------------------
# The core: used by both /ask and /sms
# ---------------------------------------------------------------------------

WORD_PATTERN = re.compile(r"[^\W_]+")


def looks_like_answer(text):
    """A short message can be a yes/no/skip answer. A longer one only when every word is an answer word."""
    words = WORD_PATTERN.findall(text or "")
    if len(words) <= 3:
        return True
    return len(text or "") <= 12 and all(eligibility.parse_yes_no(word) != "unclear" for word in words)


def is_bus_word(text):
    """True if the whole message is one of the words that asks for bus directions."""
    cleaned = (text or "").strip().strip("?!.¿¡。？！").strip().lower()
    return cleaned in BUS_WORDS


def choose_intent(understood, session, text):
    """Decide which handler to use, using the session to fill in gaps."""
    intent = understood.get("intent")
    need = understood.get("need")
    if intent in ("next_option", "help", "directions"):
        return intent
    if not need and is_bus_word(text):
        return "directions"
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
    understood = None

    # Step 2: right after a yes/no question, a short yes/no/skip answers it. Anything else is a
    # new message: a new need restarts the search, and other text ends the questions.
    if session["awaiting_answer"] is not None:
        value = eligibility.parse_yes_no(text) if looks_like_answer(text) else "unclear"
        if value == "unclear":
            understood = brain.understand(text, session["history"], lang_hint=lang)
            if not understood.get("need") and not understood.get("location_text") \
                    and understood.get("intent") not in ("next_option", "help", "directions"):
                # Not a new request: "no I am not" still answers by its first word,
                # and anything else gets the same question again.
                words = WORD_PATTERN.findall(text or "")
                value = eligibility.parse_yes_no(words[0]) if words else "unclear"
                if value == "unclear":
                    result = search_step(session, channel)
                    remember_turn(session, text, result["reply"])
                    return result
        if value != "unclear":
            if lang:
                session["language"] = lang
            session["answers"][session["awaiting_answer"]] = value
            session["awaiting_answer"] = None
            result = search_step(session, channel)
            remember_turn(session, text, result["reply"])
            return result
        session["awaiting_answer"] = None

    # Step 3: let the brain read the message.
    if understood is None:
        understood = brain.understand(text, session["history"], lang_hint=lang)

    # Step 4: pick the language. When we are waiting for a zip, a bare "77004"
    # says nothing about language, so keep the one we were using.
    if lang:
        session["language"] = lang
    elif not (session["awaiting_location"] and not understood.get("need")):
        session["language"] = understood.get("language") or "en"

    # Step 5: answer by intent.
    intent = choose_intent(understood, session, text)
    if intent == "next_option":
        result = handle_next_option(session, understood, text, channel)
    elif intent == "directions":
        result = handle_directions(session, channel)
    elif intent == "followup":
        result = handle_followup(session, text, channel)
    elif intent == "help":
        result = handle_help(session)
    elif intent == "find":
        result = handle_find(session, understood, text, channel, lat, lng)
    else:
        result = handle_other(session)

    # Step 6: remember this turn.
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
            "asking": None,
            "transit": None,
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
