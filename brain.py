"""The AI layer of HelpLine Houston.

Two jobs:
  understand(): read what the person typed and say what they want.
  answer():     write the short reply, using only the facts we give it.

Both use Gemini when a GEMINI_API_KEY is set. If there is no key, or Gemini
fails for any reason, they use simple rules instead (the "fallback").
Neither function ever raises an error to the caller.
"""

import json
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

try:
    from google import genai
    from google.genai import types
except Exception as error:  # the package is missing: we can still use the fallback
    print("brain: google-genai not available, using fallback only:", error)
    genai = None
    types = None

# Read the .env file (if there is one) so GEMINI_API_KEY can be set there.
load_dotenv()

MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

LANGUAGES = ["en", "es", "vi", "zh", "ar"]
NEEDS = ["food", "shelter", "hygiene", "clinic", "library", "cooling", "legal"]
INTENTS = ["find", "next_option", "followup", "help", "directions", "other"]

LANGUAGE_NAMES = {
    "en": "English",
    "es": "Spanish",
    "vi": "Vietnamese",
    "zh": "Chinese (simplified)",
    "ar": "Arabic",
}

# Words for each need, used by the fallback. Matching is case-insensitive.
NEED_KEYWORDS = {
    "food": [
        "food", "hungry", "eat", "meal", "pantry",
        "comida", "hambre", "comer", "despensa",
        "đồ ăn", "thức ăn", "đói", "ăn",
        "食物", "吃", "饿",
        "طعام", "أكل", "جوع", "جائع",
    ],
    "shelter": [
        "shelter", "sleep", "bed", "homeless", "housing",
        "albergue", "refugio", "dormir", "cama",
        "chỗ ở", "ngủ", "nhà ở",
        "住", "睡", "收容所",
        "مأوى", "نوم", "سرير", "ملجأ",
    ],
    "hygiene": [
        "shower", "bath", "bathroom", "restroom", "hygiene", "laundry", "toilet", "wash",
        "ducha", "ducharme", "baño", "bañar", "bañarme", "higiene", "lavar",
        "tắm", "nhà vệ sinh",
        "洗澡", "厕所", "洗手间", "淋浴",
        "استحمام", "حمام", "مرحاض", "غسيل",
    ],
    "clinic": [
        "clinic", "doctor", "medical", "health", "nurse", "sick", "medicine",
        "clínica", "médico", "medico", "salud", "enfermo",
        "phòng khám", "bác sĩ", "bệnh",
        "诊所", "医生", "看病", "医院",
        "عيادة", "طبيب", "مستشفى", "دواء",
    ],
    "library": [
        "library", "wifi", "internet", "computer", "print",
        "biblioteca", "computadora", "internet",
        "thư viện", "máy tính",
        "图书馆", "电脑",
        "مكتبة", "كمبيوتر", "إنترنت",
    ],
    "cooling": [
        "cooling", "cool", "heat", "hot", "air conditioning", "ac",
        "calor", "fresco", "refrescar", "aire acondicionado",
        "máy lạnh", "nóng", "mát",
        "降温", "凉快", "热", "空调",
        "تبريد", "تكييف", "حار", "حرارة",
    ],
    "legal": [
        "legal", "lawyer", "attorney", "immigration", "rights",
        "abogado", "derechos",
        "luật sư", "pháp lý",
        "律师", "法律",
        "محامي", "قانون",
    ],
}

# Words that ask for a human (or 211).
HELP_WORDS = [
    "help", "ayuda", "211", "human", "person", "operator",
    "persona", "humano", "operador",
    "người", "giúp", "hỗ trợ",
    "人工", "真人", "帮助",
    "مساعدة",
]

# Phrases that ask how to get to a place or which bus to take (intent "directions").
DIRECTIONS_WORDS = [
    "how do i get there", "how can i get there", "how do i get to", "how can i get to",
    "which bus", "what bus", "how far by bus", "by bus",
    "cómo llego", "como llego", "qué autobús", "que autobus", "qué bus", "cuál autobús",
    "làm sao để đến", "đi bằng xe buýt", "xe buýt nào", "đến đó",
    "怎么去", "坐哪路公交", "怎么走", "公交",
    "كيف أصل", "أي حافلة", "كيف أذهب", "حافلة",
]

# Vietnamese letters that Spanish does not use. Seeing one means Vietnamese.
VIETNAMESE_CHARS = set(
    "ăâđêôơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ"
)
SPANISH_WORDS = [
    "hola", "necesito", "quiero", "tengo", "comida", "hambre", "albergue",
    "refugio", "dormir", "ducha", "baño", "médico", "clínica", "biblioteca",
    "calor", "abogado", "ayuda", "cerca", "dónde", "donde", "abierto", "hoy",
    "mañana", "qué", "que",
]

# The "more options" hint at the end of a reply, one set per language. SMS names the number to
# reply with; the kiosk says to turn the knob. The main program adds it only when another option exists.
MORE_SMS = {
    "en": "Reply {number} for more options.",
    "es": "Responda {number} para más opciones.",
    "vi": "Trả lời {number} để xem thêm lựa chọn.",
    "zh": "回复{number}查看更多选择。",
    "ar": "أرسل {number} لمزيد من الخيارات.",
}
MORE_KIOSK = {
    "en": "Turn the knob for more options.",
    "es": "Gire la perilla para más opciones.",
    "vi": "Vặn núm để xem thêm lựa chọn.",
    "zh": "转动旋钮查看更多选择。",
    "ar": "أدر المؤشر لمزيد من الخيارات.",
}


def more_options_hint(language, channel, number):
    """'Reply 2 for more options.' on SMS (number = the option to reply with); the kiosk knob sentence otherwise."""
    if channel == "sms":
        return MORE_SMS.get(language, MORE_SMS["en"]).format(number=number)
    return MORE_KIOSK.get(language, MORE_KIOSK["en"])


# Words for each need, used inside the templates.
NEED_WORDS = {
    "en": {"food": "food", "shelter": "shelter", "hygiene": "hygiene",
           "clinic": "clinic", "library": "library", "cooling": "cooling",
           "legal": "legal help"},
    "es": {"food": "comida", "shelter": "refugio", "hygiene": "higiene",
           "clinic": "clínica", "library": "biblioteca", "cooling": "refrigeración",
           "legal": "ayuda legal"},
    "vi": {"food": "thức ăn", "shelter": "chỗ ngủ", "hygiene": "tắm rửa",
           "clinic": "phòng khám", "library": "thư viện", "cooling": "tránh nóng",
           "legal": "trợ giúp pháp lý"},
    "zh": {"food": "食物", "shelter": "住宿", "hygiene": "洗浴",
           "clinic": "诊所", "library": "图书馆", "cooling": "避暑",
           "legal": "法律援助"},
    "ar": {"food": "الطعام", "shelter": "المأوى", "hygiene": "الاستحمام",
           "clinic": "العيادة", "library": "المكتبة", "cooling": "التبريد",
           "legal": "المساعدة القانونية"},
}

# Only these fields of a match are shown to the model (no internal ids).
# "notes" is left out on purpose: it holds internal research remarks, and the model
# used them to suggest services the person did not ask for. "offers" is sent as labels only.
FACT_FIELDS = ["name", "address", "distance_mi", "open_now", "closes_at",
               "opens_at", "requirements", "phone", "offers", "hours", "hours_unknown"]

SMS_LIMIT = 300

# Gemini answer format for understand(). "none" and 0 mean "no value".
UNDERSTAND_SCHEMA = None
if types is not None:
    UNDERSTAND_SCHEMA = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "intent": types.Schema(type=types.Type.STRING, enum=INTENTS),
            "need": types.Schema(type=types.Type.STRING, enum=NEEDS + ["none"]),
            "language": types.Schema(type=types.Type.STRING, enum=LANGUAGES),
            "location_text": types.Schema(type=types.Type.STRING),
            "option_number": types.Schema(type=types.Type.INTEGER),
        },
        required=["intent", "need", "language", "location_text", "option_number"],
    )

_client = None


# ---------------------------------------------------------------------------
# Gemini helpers
# ---------------------------------------------------------------------------

def _get_client():
    """Return the Gemini client, or None if there is no key or it cannot start."""
    global _client
    if _client is not None:
        return _client
    key = os.environ.get("GEMINI_API_KEY")
    if not key or genai is None:
        return None
    try:
        _client = genai.Client(api_key=key)
    except Exception as error:
        print("brain: could not create Gemini client:", error)
        return None
    return _client


def _recent(history, count=6):
    """Keep only the last few history items."""
    if not history:
        return []
    return list(history)[-count:]


def _history_text(history):
    lines = []
    for item in _recent(history):
        who = "User" if item.get("role") == "user" else "Bot"
        lines.append(f"{who}: {item.get('text', '')}")
    return "\n".join(lines) if lines else "(none)"


# ---------------------------------------------------------------------------
# understand()
# ---------------------------------------------------------------------------

def _clean_understanding(raw, lang_hint=None):
    """Check the model's (or fallback's) answer. Bad values become defaults."""
    intent = raw.get("intent")
    if intent not in INTENTS:
        intent = "other"

    need = raw.get("need")
    if need not in NEEDS:
        need = None

    language = raw.get("language")
    if language not in LANGUAGES:
        language = "en"
    if lang_hint in LANGUAGES:
        language = lang_hint

    location_text = raw.get("location_text")
    if not isinstance(location_text, str) or not location_text.strip():
        location_text = None
    else:
        location_text = location_text.strip()

    option_number = raw.get("option_number")
    if isinstance(option_number, bool) or not isinstance(option_number, int) or option_number < 1:
        option_number = None

    return {
        "intent": intent,
        "need": need,
        "language": language,
        "location_text": location_text,
        "option_number": option_number,
    }


UNDERSTAND_PROMPT = """You read one message from a person who is looking for a free
service in Houston, Texas. Some people type, some speak. Return JSON only.

Fields:
- intent:
  - "find": a new search for a need. Example: "I'm hungry" -> find, need food.
    Example: "what about a shelter?" -> find, need shelter.
  - "next_option": the person picks one of the places already listed.
    Example: "2" -> next_option, option_number 2. "option 3" -> option_number 3.
    "the second one" -> option_number 2.
  - "directions": the person asks how to get to a place, or which bus to take.
    Example: "how do I get there?" -> directions. "which bus goes there?" -> directions.
  - "followup": a question about the places already given, not a new search.
    Example: "is it open tomorrow?" -> followup. "is it far?" -> followup.
  - "help": the person asks for a human, or 211. Example: "HELP" -> help.
    "AYUDA" -> help. "I want a person" -> help.
  - "other": anything else (greetings, thanks, unclear text).
- need: one of food, shelter, hygiene, clinic, library, cooling, legal, or "none".
  food = food, meals, hungry. shelter = a bed, sleeping, housing.
  hygiene = showers, restrooms, laundry. clinic = doctor, medical care.
  library = wifi, computers, printing. cooling = heat, hot, air conditioning.
  legal = lawyer, immigration or legal help.
- language: the language the person wrote in: en, es, vi, zh, or ar.
- location_text: place words copied from the message, such as "UH", "77004",
  "Third Ward". Use the words after near / cerca de / en / at. Empty string if none.
- option_number: the number 1-5 for next_option, otherwise 0.

If the message has no words (only a number or symbols), use the language of the recent conversation.

Recent conversation (oldest first):
{history}

Message: {text}
"""


def _understand_with_gemini(client, text, history):
    prompt = UNDERSTAND_PROMPT.format(history=_history_text(history), text=text)
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=UNDERSTAND_SCHEMA,
        temperature=0,
    )
    response = client.models.generate_content(model=MODEL, contents=prompt, config=config)
    raw = json.loads(response.text)
    # The model says "none" for no need, and 0 for no option number.
    if raw.get("need") == "none":
        raw["need"] = None
    if raw.get("option_number") == 0:
        raw["option_number"] = None
    if raw.get("location_text") == "":
        raw["location_text"] = None
    return raw


def _detect_language(text):
    """Guess the language from the letters used. Default is English."""
    if re.search(r"[一-鿿]", text):
        return "zh"
    if re.search(r"[؀-ۿ]", text):
        return "ar"
    lowered = text.lower()
    if any(ch in VIETNAMESE_CHARS for ch in lowered):
        return "vi"
    if re.search(r"[ñ¿¡]", lowered) or re.search(r"[áéíóú]", lowered):
        return "es"
    for word in SPANISH_WORDS:
        if re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", lowered):
            return "es"
    return "en"


def _find_need(lowered):
    for need in NEEDS:
        for word in NEED_KEYWORDS[need]:
            if _has_word(lowered, word):
                return need
    return None


def _has_word(lowered, word):
    """Whole-word match, except Chinese and Arabic words (plain search).

    Whole words matter for Vietnamese: "ăn" (eat) must not match inside
    "khăn" (towel) or "chăn" (blanket).
    """
    if re.search(r"[\u4e00-\u9fff\u0600-\u06ff]", word):
        return word in lowered
    return re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", lowered) is not None


def _find_option_number(text):
    lowered = text.lower().strip()
    match = re.match(r"^(?:option|opción|opcion|número|numero|#)?\s*([1-5])\.?$", lowered)
    if match:
        return int(match.group(1))
    # "the second one" -> 2
    ordinal = re.match(r"^the (first|second|third|fourth|fifth) one$", lowered)
    if ordinal:
        return {"first": 1, "second": 2, "third": 3,
                "fourth": 4, "fifth": 5}[ordinal.group(1)]
    return None


def _find_location(text):
    """Pull the place words out of the message, or a zip code."""
    match = re.search(r"\b(?:near|around|cerca de|cerca|at|en)\s+(.+)$", text, re.IGNORECASE)
    if match:
        place = match.group(1)
        # Stop at a time word: "near UH tonight" -> "UH".
        place = re.split(r"\b(?:hoy|mañana|manana|tonight|today|tomorrow|now|ahora|please|por favor)\b",
                         place, flags=re.IGNORECASE)[0]
        place = place.strip().rstrip("?.!,").strip()
        # "en la noche" and "at 5pm" are times, not places.
        is_time = re.match(r"^(la|el)?\s*(noche|tarde|mañana)$|^\d{1,2}\s*(am|pm)$",
                           place, re.IGNORECASE)
        if place and not is_time:
            return place
    zip_match = re.search(r"\b\d{5}\b", text)
    if zip_match:
        return zip_match.group(0)
    return None


def _understand_with_rules(text, history):
    """The fallback: keywords and simple patterns, no AI."""
    clean_text = (text or "").strip()
    lowered = clean_text.lower()

    language = _detect_language(clean_text)
    # A message like "2" has no letters, so use the language of the last user message.
    if not re.search(r"[^\W\d_]", clean_text):
        for item in reversed(history or []):
            if item.get("role") == "user" and item.get("text"):
                language = _detect_language(item["text"])
                break

    option_number = _find_option_number(clean_text)
    if option_number is not None:
        return {"intent": "next_option", "need": None, "language": language,
                "location_text": None, "option_number": option_number}

    need = _find_need(lowered)
    location_text = _find_location(clean_text)

    if need is None and any(_has_word(lowered, word) for word in HELP_WORDS):
        intent = "help"
    elif need is not None:
        intent = "find"
    elif any(_has_word(lowered, phrase) for phrase in DIRECTIONS_WORDS):
        intent = "directions"
    elif "?" in clean_text:
        intent = "followup"
    else:
        intent = "other"

    return {"intent": intent, "need": need, "language": language,
            "location_text": location_text, "option_number": None}


def understand(text: str, history: list[dict], lang_hint: str | None = None) -> dict:
    """Work out what the person wants. Always returns the 5 keys from the contract."""
    try:
        history = _recent(history)
        client = _get_client()
        if client is not None:
            try:
                raw = _understand_with_gemini(client, text or "", history)
                return _clean_understanding(raw, lang_hint)
            except Exception as error:
                print("brain: Gemini understand failed, using fallback:", error)
        raw = _understand_with_rules(text, history)
        return _clean_understanding(raw, lang_hint)
    except Exception as error:
        print("brain: understand failed:", error)
        return _clean_understanding({}, lang_hint)


# ---------------------------------------------------------------------------
# answer()
# ---------------------------------------------------------------------------

def _to_12h(hhmm):
    """'18:00' -> '6 PM', '09:30' -> '9:30 AM'."""
    if not hhmm:
        return ""
    hour_text, minute_text = hhmm.split(":")[:2]
    hour, minute = int(hour_text), int(minute_text)
    if hour == 24 or (hour == 0 and minute == 0):
        return "midnight"
    suffix = "AM" if hour < 12 else "PM"
    hour12 = hour % 12 or 12
    if minute == 0:
        return f"{hour12} {suffix}"
    return f"{hour12}:{minute:02d} {suffix}"


HOUSTON = ZoneInfo("America/Chicago")

# The day abbreviations the data uses, in weekday order (Monday first), and their translations.
DAY_NAMES_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAY_NAMES = {
    "en": DAY_NAMES_EN,
    "es": ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"],
    "vi": ["T2", "T3", "T4", "T5", "T6", "T7", "CN"],
    "zh": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"],
    "ar": ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"],
}
# "today" and "tomorrow" in each language (used when the opening is that day, Houston time).
RELATIVE_DAYS = {
    "en": ("today", "tomorrow"), "es": ("hoy", "mañana"), "vi": ("hôm nay", "ngày mai"),
    "zh": ("今天", "明天"), "ar": ("اليوم", "غدا"),
}


def _opens_text(opens_at, language="en", now=None):
    """'Mon 09:00' -> 'Mon 9 AM' (in the language: 'hoy 9 AM', 'mañana 9 AM', 'Lun 9 AM' ...).

    'today' is used only when the opening is later today, and 'tomorrow' for the next day.
    `now` is for tests; it defaults to the current time in Houston.
    """
    if not opens_at:
        return ""
    parts = opens_at.split(" ")
    if len(parts) != 2 or parts[0] not in DAY_NAMES_EN:
        return _to_12h(opens_at)
    day_index = DAY_NAMES_EN.index(parts[0])
    hhmm = parts[1]
    lang = language if language in DAY_NAMES else "en"
    now = now or datetime.now(HOUSTON)
    today = now.weekday()
    open_minutes = int(hhmm[:2]) * 60 + int(hhmm[3:5])
    today_word, tomorrow_word = RELATIVE_DAYS[lang]
    if day_index == today and open_minutes > now.hour * 60 + now.minute:
        label = today_word
    elif day_index == (today + 1) % 7:
        label = tomorrow_word
    else:
        label = DAY_NAMES[lang][day_index]
    return f"{label} {_to_12h(hhmm)}"


def _closes_text(match):
    closes = match.get("closes_at")
    if not closes or closes == "24:00":
        return None
    return _to_12h(closes)


def _miles(match):
    return f"{float(match.get('distance_mi', 0)):.1f}"


def distance_phrase(language, miles):
    """'1 mile away' / '0.8 miles away' in the language, with the singular where it applies."""
    value = float(miles)
    one = round(value, 1) == 1.0
    if language == "es":
        return "a 1 milla" if one else f"a {value:.1f} millas"
    if language == "vi":
        return f"cách {value:.1f} dặm"
    if language == "zh":
        return f"距离{value:.1f}英里"
    if language == "ar":
        return "على بعد ميل واحد" if one else f"على بعد {value:.1f} ميل"
    return "1 mile away" if one else f"{value:.1f} miles away"


def distance_text(language, place):
    """The place's distance (its distance_mi field) as a phrase in the language."""
    return distance_phrase(language, float(place.get("distance_mi") or 0))


def _fit_sms(build, name):
    """Build the SMS text, shortening the name until it fits SMS_LIMIT characters."""
    text = build(name)
    for limit in (120, 60, 30):
        if len(text) <= SMS_LIMIT:
            break
        text = build(name[:limit])
    if len(text) > SMS_LIMIT:
        text = text[:SMS_LIMIT - 3] + "..."
    return text


# Sentence pieces for the no-Gemini replies, one set per language.
# {name}, {mi}, {closes}, {need}, {opens}, {dist} and {phone} are filled in. A reply names a place with
# one sentence: no address and no phone. Only the hours-unknown sentence keeps the phone, because
# calling is the only way to learn those hours. Offers and the "more options" hint come after these
# sentences; main.py adds them.
TEMPLATES = {
    "en": {
        "sms_open": "{name} - open now, {mi} mi away.",
        "sms_open_closes": "{name} - open now, {mi} mi away, closes {closes}.",
        "kiosk_open": "{name} is open now, {mi} miles away.",
        "kiosk_open_closes": "{name} is open now, {mi} miles away, closes at {closes}.",
        "next_none": "Nothing for {need} is open right now.",
        "next_after_fit": "The next one to open is {name}",
        "next_opens": ", opening {opens}",
        "next_dist": ", {dist}.",
        "this_service": "this service",
        "not_found": "I could not find an open place right now. Please call 211 for help.",
        "unknown_hours": "{name}, {mi} miles away, hours not listed, call {phone} first.",
        "unknown_hours_nophone": "{name}, {mi} miles away, hours not listed.",
        "offers": "Offers: {items}.", "offers_sep": ", ",
    },
    "es": {
        "sms_open": "{name}: abierto ahora, a {mi} mi.",
        "sms_open_closes": "{name}: abierto ahora, a {mi} mi, cierra a las {closes}.",
        "kiosk_open": "{name} está abierto ahora, a {mi} millas.",
        "kiosk_open_closes": "{name} está abierto ahora, a {mi} millas, cierra a las {closes}.",
        "next_none": "Nada de {need} está abierto ahora.",
        "next_after_fit": "El siguiente en abrir es {name}",
        "next_opens": ", abre {opens}",
        "next_dist": ", {dist}.",
        "this_service": "este servicio",
        "not_found": "No encontré un lugar abierto ahora. Llame al 211 para ayuda.",
        "unknown_hours": "{name}, a {mi} millas, horario no publicado, llame primero al {phone}.",
        "unknown_hours_nophone": "{name}, a {mi} millas, horario no publicado.",
        "offers": "Ofrece: {items}.", "offers_sep": ", ",
    },
    "vi": {
        "sms_open": "{name}: đang mở cửa, cách {mi} dặm.",
        "sms_open_closes": "{name}: đang mở cửa, cách {mi} dặm, đóng cửa lúc {closes}.",
        "kiosk_open": "{name} đang mở cửa, cách {mi} dặm.",
        "kiosk_open_closes": "{name} đang mở cửa, cách {mi} dặm, đóng cửa lúc {closes}.",
        "next_none": "Hiện không có nơi {need} nào mở cửa.",
        "next_after_fit": "Nơi tiếp theo mở cửa là {name}",
        "next_opens": ", mở cửa {opens}",
        "next_dist": ", {dist}.",
        "this_service": "dịch vụ này",
        "not_found": "Hiện tôi không tìm thấy nơi nào đang mở cửa. Vui lòng gọi 211 để được giúp.",
        "unknown_hours": "{name}, cách {mi} dặm, chưa có giờ mở cửa, hãy gọi {phone} trước.",
        "unknown_hours_nophone": "{name}, cách {mi} dặm, chưa có giờ mở cửa.",
        "offers": "Dịch vụ: {items}.", "offers_sep": ", ",
    },
    "zh": {
        "sms_open": "{name}：现在开放，距离{mi}英里。",
        "sms_open_closes": "{name}：现在开放，距离{mi}英里，{closes}关门。",
        "kiosk_open": "{name}现在开放，距离{mi}英里。",
        "kiosk_open_closes": "{name}现在开放，距离{mi}英里，{closes}关门。",
        "next_none": "现在没有开放的{need}地点。",
        "next_after_fit": "下一个开放的是{name}",
        "next_opens": "，{opens}开门",
        "next_dist": "，{dist}。",
        "this_service": "这项服务",
        "not_found": "现在找不到开放的地点。请拨打211寻求帮助。",
        "unknown_hours": "{name}，距离{mi}英里，未列出营业时间，请先致电{phone}。",
        "unknown_hours_nophone": "{name}，距离{mi}英里，未列出营业时间。",
        "offers": "提供：{items}。", "offers_sep": "、",
    },
    "ar": {
        "sms_open": "{name}: مفتوح الآن، على بعد {mi} ميل.",
        "sms_open_closes": "{name}: مفتوح الآن، على بعد {mi} ميل، يغلق الساعة {closes}.",
        "kiosk_open": "{name} مفتوح الآن، على بعد {mi} ميل.",
        "kiosk_open_closes": "{name} مفتوح الآن، على بعد {mi} ميل، يغلق الساعة {closes}.",
        "next_none": "لا يوجد مكان مفتوح الآن لـ{need}.",
        "next_after_fit": "المكان التالي الذي يفتح هو {name}",
        "next_opens": "، يفتح {opens}",
        "next_dist": "، {dist}.",
        "this_service": "هذه الخدمة",
        "not_found": "لم أجد مكانا مفتوحا الآن. يرجى الاتصال بالرقم 211 للمساعدة.",
        "unknown_hours": "{name}، على بعد {mi} ميل، ساعات العمل غير مدرجة، اتصل أولا بالرقم {phone}.",
        "unknown_hours_nophone": "{name}، على بعد {mi} ميل، ساعات العمل غير مدرجة.",
        "offers": "تقدم: {items}.", "offers_sep": "، ",
    },
}

# The fixed list of offers a place can have (the "offers" field in data/resources.json).
# scripts/validate.py has its own copy; tests check that the two lists match.
OFFER_KEYS = [
    "food_pantry", "hot_meals", "shelter_beds", "showers", "laundry", "restrooms", "clothing",
    "wifi", "computers", "phone_charging", "mail", "id_help", "medical_care", "dental_care",
    "pharmacy", "mental_health", "legal_aid", "immigration_help", "case_management", "job_help",
    "cooling", "heating", "hygiene_items", "baby_supplies",
]

# Short labels for each offer, one set per language (used in the "Offers: ..." line and for Gemini).
OFFER_LABELS = {
    "en": {
        "food_pantry": "food pantry", "hot_meals": "free meals", "shelter_beds": "shelter beds",
        "showers": "showers", "laundry": "laundry", "restrooms": "restrooms", "clothing": "clothing",
        "wifi": "Wi-Fi", "computers": "computers", "phone_charging": "phone charging", "mail": "mail",
        "id_help": "ID help", "medical_care": "medical care", "dental_care": "dental care",
        "pharmacy": "pharmacy", "mental_health": "mental health", "legal_aid": "legal aid",
        "immigration_help": "immigration help", "case_management": "case management",
        "job_help": "job help", "cooling": "cooling center", "heating": "heating",
        "hygiene_items": "hygiene items", "baby_supplies": "baby supplies",
    },
    "es": {
        "food_pantry": "despensa de comida", "hot_meals": "comidas gratis", "shelter_beds": "camas de refugio",
        "showers": "duchas", "laundry": "lavandería", "restrooms": "baños", "clothing": "ropa",
        "wifi": "Wi-Fi", "computers": "computadoras", "phone_charging": "cargar el teléfono", "mail": "correo",
        "id_help": "ayuda con identificación", "medical_care": "atención médica", "dental_care": "dentista",
        "pharmacy": "farmacia", "mental_health": "salud mental", "legal_aid": "ayuda legal",
        "immigration_help": "ayuda de inmigración", "case_management": "manejo de casos",
        "job_help": "ayuda para empleo", "cooling": "centro de enfriamiento", "heating": "calefacción",
        "hygiene_items": "artículos de higiene", "baby_supplies": "artículos para bebés",
    },
    "vi": {
        "food_pantry": "kho lương thực", "hot_meals": "bữa ăn miễn phí", "shelter_beds": "giường ngủ",
        "showers": "chỗ tắm", "laundry": "giặt ủi", "restrooms": "nhà vệ sinh", "clothing": "quần áo",
        "wifi": "Wi-Fi", "computers": "máy tính", "phone_charging": "sạc điện thoại", "mail": "thư từ",
        "id_help": "hỗ trợ giấy tờ", "medical_care": "chăm sóc y tế", "dental_care": "nha khoa",
        "pharmacy": "nhà thuốc", "mental_health": "sức khỏe tâm thần", "legal_aid": "trợ giúp pháp lý",
        "immigration_help": "hỗ trợ nhập cư", "case_management": "quản lý hồ sơ",
        "job_help": "hỗ trợ việc làm", "cooling": "nơi tránh nóng", "heating": "sưởi ấm",
        "hygiene_items": "đồ vệ sinh", "baby_supplies": "đồ dùng cho bé",
    },
    "zh": {
        "food_pantry": "食品库", "hot_meals": "免费热餐", "shelter_beds": "床位",
        "showers": "洗澡", "laundry": "洗衣", "restrooms": "洗手间", "clothing": "衣物",
        "wifi": "无线网络", "computers": "电脑", "phone_charging": "手机充电", "mail": "邮件",
        "id_help": "证件帮助", "medical_care": "医疗服务", "dental_care": "牙科",
        "pharmacy": "药房", "mental_health": "心理健康", "legal_aid": "法律援助",
        "immigration_help": "移民帮助", "case_management": "个案管理", "job_help": "求职帮助",
        "cooling": "避暑中心", "heating": "取暖", "hygiene_items": "卫生用品", "baby_supplies": "婴儿用品",
    },
    "ar": {
        "food_pantry": "بنك طعام", "hot_meals": "وجبات ساخنة مجانية", "shelter_beds": "أسرّة إيواء",
        "showers": "الاستحمام", "laundry": "غسيل الملابس", "restrooms": "دورات المياه", "clothing": "ملابس",
        "wifi": "واي فاي", "computers": "حواسيب", "phone_charging": "شحن الهاتف", "mail": "البريد",
        "id_help": "المساعدة في الهوية", "medical_care": "رعاية طبية", "dental_care": "رعاية الأسنان",
        "pharmacy": "صيدلية", "mental_health": "الصحة النفسية", "legal_aid": "مساعدة قانونية",
        "immigration_help": "مساعدة الهجرة", "case_management": "إدارة الحالات",
        "job_help": "مساعدة في العمل", "cooling": "مركز تبريد", "heating": "تدفئة",
        "hygiene_items": "مستلزمات النظافة", "baby_supplies": "مستلزمات الأطفال",
    },
}

# The "Offers" line lists at most this many offers.
OFFERS_ON_LINE = 4


def offer_labels(place, language):
    """The labels of a place's offers in this language, in the data's order, without repeats.

    Unknown keys and bad entries are skipped. Returns [] when the place lists no offers.
    """
    labels = OFFER_LABELS.get(language, OFFER_LABELS["en"])
    names = []
    for offer in place.get("offers") or []:
        key = offer.get("key") if isinstance(offer, dict) else None
        if key in labels and labels[key] not in names:
            names.append(labels[key])
    return names


def offers_line(place, language):
    """'Offers: food pantry, showers, Wi-Fi.' with up to OFFERS_ON_LINE offers, or None if there are none."""
    names = offer_labels(place, language)[:OFFERS_ON_LINE]
    if not names:
        return None
    words = TEMPLATES.get(language, TEMPLATES["en"])
    return words["offers"].format(items=words["offers_sep"].join(names))


def _unknown_text(language, first, name=None):
    """'<name>, 0.8 miles away, hours not listed, call <phone> first.' (never says open)."""
    words = TEMPLATES.get(language, TEMPLATES["en"])
    phone = first.get("phone")
    shown = name if name is not None else first.get("name", "")
    if phone:
        return words["unknown_hours"].format(name=shown, mi=_miles(first), phone=phone)
    return words["unknown_hours_nophone"].format(name=shown, mi=_miles(first))


def _is_next_place(match):
    """True for a place that is closed now and opens later (the next place to open, from the search)."""
    return bool(match.get("opens_at")) and not match.get("open_now") and not match.get("hours_unknown")


def _template_sms(language, matches, unmet_next, need):
    """Plain sentences for SMS, in the person's language. main.py adds the offers and the hint."""
    words = TEMPLATES.get(language, TEMPLATES["en"])
    if matches and matches[0].get("hours_unknown"):
        first = matches[0]
        return _fit_sms(lambda name_part: _unknown_text(language, first, name_part), first.get("name", ""))
    if matches and _is_next_place(matches[0]):
        first = matches[0]
        return _fit_sms(lambda name_part: next_open_text(language, dict(first, name=name_part)),
                        first.get("name", ""))
    if matches:
        first = matches[0]
        closes = _closes_text(first)

        def build(name_part):
            if closes:
                return words["sms_open_closes"].format(name=name_part, mi=_miles(first), closes=closes)
            return words["sms_open"].format(name=name_part, mi=_miles(first))

        return _fit_sms(build, first.get("name", ""))
    if unmet_next:
        return _fit_sms(lambda name_part: _nothing_open_text(language, dict(unmet_next, name=name_part), need),
                        unmet_next.get("name", ""))
    return words["not_found"]


def next_open_text(language, next_place):
    """'The next one to open is X, opening tomorrow 9 AM, 2.8 miles away.' No address and no phone."""
    words = TEMPLATES.get(language, TEMPLATES["en"])
    text = words["next_after_fit"].format(name=next_place.get("name", ""))
    opens = _opens_text(next_place.get("opens_at"), language)
    if opens:
        text += words["next_opens"].format(opens=opens)
    return text + words["next_dist"].format(dist=distance_text(language, next_place))


def _nothing_open_text(language, next_place, need):
    """'Nothing for food is open right now. The next one to open is X, ...' (used when no open place is given)."""
    words = TEMPLATES.get(language, TEMPLATES["en"])
    needed = words["this_service"]
    if need:
        needed = NEED_WORDS.get(language, NEED_WORDS["en"]).get(need, needed)
    return words["next_none"].format(need=needed) + " " + next_open_text(language, next_place)


def _template_kiosk(language, matches, unmet_next, need):
    """One or two short spoken sentences. No links, no emoji. main.py adds the offers and the hint."""
    words = TEMPLATES.get(language, TEMPLATES["en"])
    if matches and matches[0].get("hours_unknown"):
        return _unknown_text(language, matches[0])
    if matches and _is_next_place(matches[0]):
        return next_open_text(language, matches[0])
    if matches:
        first = matches[0]
        closes = _closes_text(first)
        if closes:
            return words["kiosk_open_closes"].format(name=first.get("name", ""), mi=_miles(first), closes=closes)
        return words["kiosk_open"].format(name=first.get("name", ""), mi=_miles(first))
    if unmet_next:
        return _nothing_open_text(language, unmet_next, need)
    return words["not_found"]


def _facts_for(match, number, language):
    facts = {"option": number}
    for field in FACT_FIELDS:
        if field not in match:
            continue
        if field == "offers":
            facts[field] = offer_labels(match, language)  # labels only, never the quotes or links
        else:
            facts[field] = match[field]
    return facts


ANSWER_PROMPT = """You are HelpLine Houston. You help people in Houston find free services.
Write your reply in {language_name}.

Rules:
- Use ONLY the facts in the JSON below. Never invent names, addresses, hours or phone numbers.
- The first option is the one you recommend. Other options are alternatives.
- Write at most 2 short sentences. The first one names the place and says whether it is open now
  or when it opens, with its distance in miles. Use the second only if it answers a follow-up question.
- Say times in 12-hour style, like "6 PM".
- If the question is a follow-up, answer that question using the facts.
- Never give an address, a phone number, eligibility notes, or bus or walking directions.
- Never say to call a place to confirm its hours.
- Do not list the offers, and do not say that other options exist. The app adds an "Offers" line
  and a "more options" line after your reply.
- If channel is "sms": at most 300 characters.
- If channel is "kiosk": the reply is spoken aloud. No web addresses, no emoji, no lists.
- If a place has hours_unknown true, its hours are not listed. Never say it is open.
  Say its name, its distance, that its hours are not listed, and its phone number to call.
- If options is empty and unmet_next is given: say nothing is open right now,
  then the name, when the next one opens, and its distance.
- If options is empty and unmet_next is null: say nothing was found and suggest calling 211.
- If open_now is false and opens_at is given, say the place is not open now and when it opens.
- Describe a place only with the offers listed in its "offers" field. Never suggest a
  service (beds, meals, etc.) that is not listed or not what the person asked for.

Facts:
{facts}
"""


def _answer_with_gemini(client, question, language, need, matches, channel, unmet_next, history):
    facts = {
        "channel": channel,
        "need": need,
        "question": question,
        "recent_chat": _recent(history),
        "options": [_facts_for(m, i + 1, language) for i, m in enumerate(matches)],
        "unmet_next": _facts_for(unmet_next, 0, language) if unmet_next else None,
    }
    prompt = ANSWER_PROMPT.format(
        language_name=LANGUAGE_NAMES[language],
        facts=json.dumps(facts, ensure_ascii=False, indent=1, default=str),
    )
    config = types.GenerateContentConfig(temperature=0.2)
    response = client.models.generate_content(model=MODEL, contents=prompt, config=config)
    reply = (response.text or "").strip()
    if not reply:
        raise ValueError("empty reply from Gemini")
    return reply


def _check_gemini_reply(reply, channel, matches):
    """Return the reply if it follows the channel rules and leaks no address or phone, otherwise None."""
    for match in matches:
        if match.get("address") and match["address"] in reply:
            return None
        if match.get("phone") and not match.get("hours_unknown") and match["phone"] in reply:
            return None
    if channel == "sms":
        if len(reply) > SMS_LIMIT:
            return None
        return reply
    if channel == "kiosk":
        lowered = reply.lower()
        if "http" in lowered or "www." in lowered:
            return None
        if re.search(r"[\U0001F000-\U0001FAFF☀-➿️]", reply) or len(reply) > 350:
            return None
    return reply


def answer(question: str, language: str, need: str | None, matches: list[dict],
           channel: str, unmet_next: dict | None = None, history: list[dict] | None = None) -> str:
    """Write the main sentence of the reply, using only the facts in matches / unmet_next.

    The offers line and the "more options" hint are added by main.py, so they are not part of this text.
    """
    if language not in LANGUAGES:
        language = "en"
    matches = matches or []
    try:
        client = _get_client()
        if client is not None:
            try:
                reply = _answer_with_gemini(client, question, language, need,
                                            matches, channel, unmet_next, history)
                checked = _check_gemini_reply(reply, channel, matches)
                if checked is not None:
                    return checked
                print("brain: Gemini reply broke the channel rules, using template")
            except Exception as error:
                print("brain: Gemini answer failed, using template:", error)
        if channel == "sms":
            return _template_sms(language, matches, unmet_next, need)
        return _template_kiosk(language, matches, unmet_next, need)
    except Exception as error:
        print("brain: answer failed:", error)
        return "Sorry, something went wrong. Please call 211 for help."
