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
INTENTS = ["find", "next_option", "followup", "help", "other"]

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

# Words for "other options" at the end of an SMS, one per language.
REPLY_HINTS = {
    "en": "Reply 2 or 3 for other options.",
    "es": "Responda 2 o 3 para otras opciones.",
    "vi": "Trả lời 2 hoặc 3 để xem chỗ khác.",
    "zh": "回复2或3查看其他选择。",
    "ar": "أرسل 2 أو 3 لخيارات أخرى.",
}

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
FACT_FIELDS = ["name", "address", "distance_mi", "open_now", "closes_at",
               "opens_at", "requirements", "phone", "notes", "hours"]

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


def _opens_text(opens_at):
    """'Mon 09:00' -> 'Mon 9 AM'."""
    if not opens_at:
        return ""
    parts = opens_at.split(" ")
    if len(parts) == 2:
        return f"{parts[0]} {_to_12h(parts[1])}"
    return _to_12h(opens_at)


def _closes_text(match):
    closes = match.get("closes_at")
    if not closes or closes == "24:00":
        return None
    return _to_12h(closes)


def _miles(match):
    return f"{float(match.get('distance_mi', 0)):.1f}"


def _fit_sms(build, name, address):
    """Build the SMS text, shortening the address and name until it fits 300 chars."""
    text = build(name, address)
    for limit in (120, 60, 30):
        if len(text) <= SMS_LIMIT:
            break
        text = build(name[:limit], address[:limit])
    if len(text) > SMS_LIMIT:
        text = text[:SMS_LIMIT - 3] + "..."
    return text


# Sentence pieces for the no-Gemini replies, one set per language.
# {name}, {mi}, {closes}, {address}, {phone}, {need}, {opens} are filled in.
TEMPLATES = {
    "en": {
        "sms_address": ". {address}.",
        "kiosk_open": "{name} is open now, {mi} miles away", "kiosk_closes": ", closes at {closes}",
        "kiosk_address": ". Address: {address}.",
        "sms_open": "{name} - open now, {mi} mi away", "sms_closes": ", closes {closes}",
        "phone": " Phone {phone}.",
        "next_none": "Nothing for {need} is open right now. The next one is {name}",
        "next_opens": ", opening {opens}", "next_at": ", at {address}.",
        "this_service": "this service",
        "not_found": "I could not find an open place right now. Please call 211 for help.",
    },
    "es": {
        "sms_address": ". {address}.",
        "kiosk_open": "{name} está abierto ahora, a {mi} millas", "kiosk_closes": ", cierra a las {closes}",
        "kiosk_address": ". Dirección: {address}.",
        "sms_open": "{name}: abierto ahora, a {mi} mi", "sms_closes": ", cierra a las {closes}",
        "phone": " Tel {phone}.",
        "next_none": "Nada de {need} está abierto ahora. El siguiente es {name}",
        "next_opens": ", abre {opens}", "next_at": ", en {address}.",
        "this_service": "este servicio",
        "not_found": "No encontré un lugar abierto ahora. Llame al 211 para ayuda.",
    },
    "vi": {
        "sms_address": ". {address}.",
        "kiosk_open": "{name} đang mở cửa, cách {mi} dặm", "kiosk_closes": ", đóng cửa lúc {closes}",
        "kiosk_address": ". Địa chỉ: {address}.",
        "sms_open": "{name}: đang mở cửa, cách {mi} dặm", "sms_closes": ", đóng cửa lúc {closes}",
        "phone": " ĐT {phone}.",
        "next_none": "Hiện không có nơi {need} nào mở cửa. Nơi tiếp theo là {name}",
        "next_opens": ", mở cửa {opens}", "next_at": ", tại {address}.",
        "this_service": "dịch vụ này",
        "not_found": "Hiện tôi không tìm thấy nơi nào đang mở cửa. Vui lòng gọi 211 để được giúp.",
    },
    "zh": {
        "sms_address": "。{address}。",
        "kiosk_open": "{name}现在开放，距离{mi}英里", "kiosk_closes": "，{closes}关门",
        "kiosk_address": "。地址：{address}。",
        "sms_open": "{name}：现在开放，距离{mi}英里", "sms_closes": "，{closes}关门",
        "phone": " 电话 {phone}。",
        "next_none": "现在没有开放的{need}地点。下一个是{name}",
        "next_opens": "，{opens}开门", "next_at": "，地址：{address}。",
        "this_service": "这项服务",
        "not_found": "现在找不到开放的地点。请拨打211寻求帮助。",
    },
    "ar": {
        "sms_address": ". {address}.",
        "kiosk_open": "{name} مفتوح الآن، على بعد {mi} ميل", "kiosk_closes": "، يغلق الساعة {closes}",
        "kiosk_address": ". العنوان: {address}.",
        "sms_open": "{name}: مفتوح الآن، على بعد {mi} ميل", "sms_closes": "، يغلق الساعة {closes}",
        "phone": " هاتف {phone}.",
        "next_none": "لا يوجد مكان مفتوح الآن لـ{need}. المكان التالي هو {name}",
        "next_opens": "، يفتح {opens}", "next_at": "، في {address}.",
        "this_service": "هذه الخدمة",
        "not_found": "لم أجد مكانا مفتوحا الآن. يرجى الاتصال بالرقم 211 للمساعدة.",
    },
}


def _template_sms(language, matches, unmet_next, need):
    """Plain sentences for SMS, in the person's language."""
    words = TEMPLATES.get(language, TEMPLATES["en"])
    if matches:
        first = matches[0]
        name = first.get("name", "")
        address = first.get("address", "")
        phone = first.get("phone")
        closes = _closes_text(first)
        many = len(matches) > 1

        def build(name_part, address_part):
            text = words["sms_open"].format(name=name_part, mi=_miles(first))
            if closes:
                text += words["sms_closes"].format(closes=closes)
            text += words["sms_address"].format(address=address_part)
            if phone:
                text += words["phone"].format(phone=phone)
            if many:
                text += " " + REPLY_HINTS.get(language, REPLY_HINTS["en"])
            return text

        return _fit_sms(build, name, address)

    if unmet_next:
        # Same as above: shorten the name and address until it fits 300 chars.
        def build_next(name_part, address_part):
            return _next_open_text(language, unmet_next, need, name_part, address_part)
        return _fit_sms(build_next, unmet_next.get("name", ""), unmet_next.get("address", ""))

    return words["not_found"]


def _next_open_text(language, next_place, need, name=None, address=None):
    """'Nothing is open now; the next one is X, opening at Y, at Z.'

    name and address can be passed in shortened (for SMS); by default they
    come from next_place.
    """
    words = TEMPLATES.get(language, TEMPLATES["en"])
    if name is None:
        name = next_place.get("name", "")
    if address is None:
        address = next_place.get("address", "")
    opens = _opens_text(next_place.get("opens_at"))
    phone = next_place.get("phone")
    need_names = NEED_WORDS.get(language, NEED_WORDS["en"])
    needed = need_names.get(need, words["this_service"]) if need else words["this_service"]
    text = words["next_none"].format(need=needed, name=name)
    if opens:
        text += words["next_opens"].format(opens=opens)
    text += words["next_at"].format(address=address)
    if phone:
        text += words["phone"].format(phone=phone)
    return text


def _template_kiosk(language, matches, unmet_next, need):
    """One or two short spoken sentences. No links, no emoji."""
    words = TEMPLATES.get(language, TEMPLATES["en"])
    if matches:
        first = matches[0]
        closes = _closes_text(first)
        text = words["kiosk_open"].format(name=first.get("name", ""), mi=_miles(first))
        if closes:
            text += words["kiosk_closes"].format(closes=closes)
        return text + words["kiosk_address"].format(address=first.get("address", ""))
    if unmet_next:
        return _next_open_text(language, unmet_next, need)
    return words["not_found"]


def _facts_for(match, number):
    facts = {"option": number}
    for field in FACT_FIELDS:
        if field in match:
            facts[field] = match[field]
    return facts


ANSWER_PROMPT = """You are HelpLine Houston. You help people in Houston find free services.
Write your reply in {language_name}.

Rules:
- Use ONLY the facts in the JSON below. Never invent names, addresses, hours or phone numbers.
- The first option is the one you recommend. Other options are alternatives.
- Distances are in miles. Say times in 12-hour style, like "6 PM".
- If the question is a follow-up, answer that question using the facts.
- If channel is "sms": at most 300 characters. Include the place name, distance,
  closing time and address. If there is more than one option, end with this
  sentence in {language_name}: "{hint}"
- If channel is "kiosk": one or two short sentences to be spoken aloud.
  No web addresses, no emoji, no lists.
- If options is empty and unmet_next is given: say nothing is open right now,
  and say the name, when and where the next one opens.
- If options is empty and unmet_next is null: say nothing was found and suggest calling 211.

Facts:
{facts}
"""


def _answer_with_gemini(client, question, language, need, matches, channel, unmet_next, history):
    facts = {
        "channel": channel,
        "need": need,
        "question": question,
        "recent_chat": _recent(history),
        "options": [_facts_for(m, i + 1) for i, m in enumerate(matches)],
        "unmet_next": _facts_for(unmet_next, 0) if unmet_next else None,
    }
    prompt = ANSWER_PROMPT.format(
        language_name=LANGUAGE_NAMES[language],
        hint=REPLY_HINTS[language],
        facts=json.dumps(facts, ensure_ascii=False, indent=1, default=str),
    )
    config = types.GenerateContentConfig(temperature=0.2)
    response = client.models.generate_content(model=MODEL, contents=prompt, config=config)
    reply = (response.text or "").strip()
    if not reply:
        raise ValueError("empty reply from Gemini")
    return reply


def _check_gemini_reply(reply, channel, matches, language):
    """Return the reply if it follows the channel rules, otherwise None."""
    if channel == "sms":
        # Add the "reply 2 or 3" hint if the model left it out.
        if len(matches) > 1 and REPLY_HINTS[language] not in reply:
            reply = reply + " " + REPLY_HINTS[language]
        if len(reply) > SMS_LIMIT:
            return None
        return reply
    if channel == "kiosk":
        lowered = reply.lower()
        if "http" in lowered or "www." in lowered:
            return None
        if re.search(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]", reply) or len(reply) > 350:
            return None
    return reply


def answer(question: str, language: str, need: str | None, matches: list[dict],
           channel: str, unmet_next: dict | None = None, history: list[dict] | None = None) -> str:
    """Write the reply text. Uses only the facts in matches / unmet_next."""
    if language not in LANGUAGES:
        language = "en"
    matches = matches or []
    try:
        client = _get_client()
        if client is not None:
            try:
                reply = _answer_with_gemini(client, question, language, need,
                                            matches, channel, unmet_next, history)
                checked = _check_gemini_reply(reply, channel, matches, language)
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
