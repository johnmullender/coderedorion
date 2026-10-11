"""Eligibility questions for help places: which questions to ask, and how the answers filter places.

Pure functions only: no network, no Gemini. Rules come from the "eligibility" list of each
resource (see CONTRACT.md, Phase 2).
"""

import re
import unicodedata

RULE_TYPES = ["id_required", "age", "serves", "zip_in", "residency", "income_limit", "note"]
MAX_QUESTIONS = 3
LANGUAGES = ["en", "es", "vi", "zh", "ar"]

ID_QUESTION = {
    "en": "Do you have a photo ID with you?",
    "es": "¿Tiene una identificación con foto?",
    "vi": "Bạn có mang theo giấy tờ tùy thân có ảnh không?",
    "zh": "您带了带照片的身份证件吗？",
    "ar": "هل معك بطاقة هوية بها صورة؟",
}

AGE_BETWEEN = {
    "en": "Are you between {a} and {b}?",
    "es": "¿Tiene entre {a} y {b} años?",
    "vi": "Bạn từ {a} đến {b} tuổi phải không?",
    "zh": "您在{a}到{b}岁之间吗？",
    "ar": "هل عمرك بين {a} و{b} سنة؟",
}

AGE_MIN = {
    "en": "Are you {a} or older?",
    "es": "¿Tiene {a} años o más?",
    "vi": "Bạn từ {a} tuổi trở lên phải không?",
    "zh": "您{a}岁或以上吗？",
    "ar": "هل عمرك {a} سنة أو أكثر؟",
}

AGE_MAX = {
    "en": "Are you {b} or younger?",
    "es": "¿Tiene {b} años o menos?",
    "vi": "Bạn {b} tuổi trở xuống phải không?",
    "zh": "您{b}岁或以下吗？",
    "ar": "هل عمرك {b} سنة أو أقل؟",
}

SERVES_QUESTIONS = {
    "women": {
        "en": "Is this for a woman, or a mother with children?",
        "es": "¿Es para una mujer o para una madre con hijos?",
        "vi": "Đây có phải dành cho phụ nữ hoặc bà mẹ có con không?",
        "zh": "这是为女性或带孩子的母亲准备的吗？",
        "ar": "هل هذا لامرأة أو لأم مع أطفالها؟",
    },
    "men": {
        "en": "Is this for a man?",
        "es": "¿Es para un hombre?",
        "vi": "Đây có phải dành cho nam giới không?",
        "zh": "这是给男性的吗？",
        "ar": "هل هذا لرجل؟",
    },
    "families": {
        "en": "Do you have children with you?",
        "es": "¿Tiene hijos con usted?",
        "vi": "Bạn có con đi cùng không?",
        "zh": "您有孩子跟您在一起吗？",
        "ar": "هل معك أطفال؟",
    },
    "youth": {
        "en": "Are you a young person (youth or young adult)?",
        "es": "¿Es usted una persona joven (joven o adulto joven)?",
        "vi": "Bạn có phải là thanh thiếu niên hoặc thanh niên không?",
        "zh": "您是青少年或年轻人吗？",
        "ar": "هل أنت من الشباب أو اليافعين؟",
    },
    "veterans": {
        "en": "Are you a veteran?",
        "es": "¿Es usted veterano?",
        "vi": "Bạn là cựu quân nhân phải không?",
        "zh": "您是退伍军人吗？",
        "ar": "هل أنت من قدامى المحاربين؟",
    },
    "seniors": {
        "en": "Are you a senior (older adult)?",
        "es": "¿Es usted una persona mayor?",
        "vi": "Bạn có phải là người cao tuổi không?",
        "zh": "您是老年人吗？",
        "ar": "هل أنت من كبار السن؟",
    },
}

RESIDENCY_QUESTION = {
    "en": "Do you live in {area}?",
    "es": "¿Vive en {area}?",
    "vi": "Bạn có sống ở {area} không?",
    "zh": "您住在{area}吗？",
    "ar": "هل تسكن في {area}؟",
}

# The English income question is copied from the source; other languages use this template
# when the question has the usual "under $X a month" form, otherwise they get the English text.
INCOME_PATTERN = re.compile(r"^Is your household income under \$([\d,]+) a month\?$")
INCOME_QUESTION = {
    "es": "¿Son los ingresos de su hogar menos de ${amount} al mes?",
    "vi": "Thu nhập hộ gia đình của bạn dưới ${amount} mỗi tháng phải không?",
    "zh": "您家庭的月收入低于${amount}吗？",
    "ar": "هل دخل أسرتك الشهري أقل من ${amount}؟",
}

# Shown instead of a question when the person skipped it.
REMINDERS = {
    "id_required": {
        "en": "Bring a photo ID if you have one.",
        "es": "Traiga una identificación con foto si tiene una.",
        "vi": "Mang theo giấy tờ tùy thân có ảnh nếu bạn có.",
        "zh": "如果有带照片的身份证件，请带上。",
        "ar": "أحضر بطاقة هوية بها صورة إن كانت لديك.",
    },
    "age": {
        "en": "Bring proof of age if you have it.",
        "es": "Traiga un comprobante de edad si lo tiene.",
        "vi": "Mang theo giấy tờ chứng minh tuổi nếu có.",
        "zh": "如果有，请带上年龄证明。",
        "ar": "أحضر ما يثبت عمرك إن توفر لديك.",
    },
    "serves": {
        "en": "Ask staff if this applies to you.",
        "es": "Pregunte al personal si esto le corresponde.",
        "vi": "Hỏi nhân viên xem điều này có áp dụng cho bạn không.",
        "zh": "请询问工作人员这是否适用于您。",
        "ar": "اسأل الموظفين إن كان هذا ينطبق عليك.",
    },
    "residency": {
        "en": "Bring proof of address if you have it.",
        "es": "Traiga un comprobante de domicilio si lo tiene.",
        "vi": "Mang theo giấy tờ chứng minh địa chỉ nếu có.",
        "zh": "如果有，请带上住址证明。",
        "ar": "أحضر ما يثبت عنوان سكنك إن توفر لديك.",
    },
    "income_limit": {
        "en": "Bring proof of income if you have it.",
        "es": "Traiga un comprobante de ingresos si lo tiene.",
        "vi": "Mang theo giấy tờ chứng minh thu nhập nếu có.",
        "zh": "如果有，请带上收入证明。",
        "ar": "أحضر ما يثبت دخلك إن توفر لديك.",
    },
}

# Words for parse_yes_no. They are normalized the same way as the reply (lower case, no accents).
YES_WORDS = ["yes", "yeah", "yep", "y", "ok", "okay", "si", "claro", "vale", "co", "vang", "da",
             "是", "是的", "对", "对的", "有", "有的", "نعم", "اجل"]
NO_WORDS = ["no", "nope", "nah", "n", "no tengo", "khong", "不", "不是", "没有", "没", "لا", "كلا"]
SKIP_WORDS = ["skip", "pass", "not sure", "dont know", "idk", "maybe", "no se", "no lo se",
              "omitir", "tal vez", "khong biet", "khong ro", "khong chac", "bo qua",
              "不知道", "不确定", "不清楚", "跳过", "لا اعرف", "لا اعلم", "تخطي"]


def _normalize(text):
    """Lower case, strip accents and apostrophes, collapse spaces. Used on both sides of a match."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("đ", "d").replace("'", "").replace("’", "")
    return " ".join(text.split())


def _has_cjk(text):
    """True if the text has a Chinese character (those words are matched without word boundaries)."""
    return re.search(r"[一-鿿]", text) is not None


_VOCAB = [
    (_normalize(word), value)
    for value, words in ((True, YES_WORDS), (False, NO_WORDS), (None, SKIP_WORDS))
    for word in words
]


def _lang(language):
    """The language if we have questions for it, else English."""
    return language if language in LANGUAGES else "en"


def question_key(rule):
    """A stable key for the question a rule asks, or None if the rule is never asked."""
    kind = rule.get("type")
    if kind == "id_required":
        return "id"
    if kind == "age":
        if rule.get("min") is None and rule.get("max") is None:
            return None  # no limit, nothing to ask
        low = "" if rule.get("min") is None else rule["min"]
        high = "" if rule.get("max") is None else rule["max"]
        return f"age:{low}-{high}"
    if kind == "serves":
        return f"serves:{rule.get('group', '')}"
    if kind == "residency":
        return f"residency:{rule.get('area', '')}"
    if kind == "income_limit":
        return f"income:{rule.get('question', '')}"
    return None  # note and zip_in are never asked


def question_text(key, rule, language):
    """The yes/no question for this rule in `language`. Raises ValueError for rules never asked."""
    if key != question_key(rule):
        raise ValueError(f"key {key!r} does not belong to this rule")
    kind = rule.get("type")
    lang = _lang(language)
    if kind == "id_required":
        return ID_QUESTION[lang]
    if kind == "age":
        low, high = rule.get("min"), rule.get("max")
        if low is not None and high is not None:
            return AGE_BETWEEN[lang].format(a=low, b=high)
        if low is not None:
            return AGE_MIN[lang].format(a=low)
        if high is not None:
            return AGE_MAX[lang].format(b=high)
        raise ValueError("age rule has no limits")
    if kind == "serves":
        group = rule.get("group", "")
        if group not in SERVES_QUESTIONS:
            return f"Do you qualify as {group}?"
        return SERVES_QUESTIONS[group][lang]
    if kind == "residency":
        return RESIDENCY_QUESTION[lang].format(area=rule.get("area", ""))
    if kind == "income_limit":
        match = INCOME_PATTERN.match(rule.get("question", ""))
        if lang == "en" or match is None:
            return rule.get("question", "")
        return INCOME_QUESTION[lang].format(amount=match.group(1))
    raise ValueError(f"rule type {kind!r} is never asked")


def check(rule, answers, zip_code):
    """True if the rule passes, False if it rules the place out, None if it still needs a question.

    answers maps question_key -> True, False, or None (skipped). Skipped counts as a pass.
    """
    if rule.get("type") == "zip_in":
        found = re.match(r"\d{5}", str(zip_code or "").strip())
        if found is None:
            return True  # no 5-digit zip known, don't block
        return found.group(0) in [str(z) for z in rule.get("zips", [])]
    key = question_key(rule)
    if key is None:
        return True  # notes, zip_in and open-ended rules never block
    if key not in answers:
        return None
    if answers[key] is None:
        return True  # skipped: counts as a pass
    return bool(answers[key])


# A "serves youth/seniors" rule says the same thing as the place's age rule, so only the age is asked.
AGE_GROUPS = {"youth", "seniors"}


def rules_to_check(place):
    """The place's rules, minus a youth or seniors rule when the place also has an age rule."""
    rules = place.get("eligibility", [])
    has_age = any(rule.get("type") == "age" and question_key(rule) for rule in rules)
    if not has_age:
        return rules
    return [rule for rule in rules
            if not (rule.get("type") == "serves" and rule.get("group") in AGE_GROUPS)]


def filter_places(places, answers, zip_code):
    """The places whose rules have not ruled them out, in the same order."""
    return [
        place for place in places
        if all(check(rule, answers, zip_code) is not False for rule in rules_to_check(place))
    ]


def next_question(places, answers, zip_code, asked):
    """The (key, rule) to ask next, or None.

    Looks at the first 3 places and picks the unanswered question that most of them have.
    Ties go to the question that appears first.
    """
    if asked >= MAX_QUESTIONS:
        return None
    counts = {}
    first_rule = {}
    for place in places[:3]:
        seen = set()
        for rule in rules_to_check(place):
            key = question_key(rule)
            if key is None or key in seen or check(rule, answers, zip_code) is not None:
                continue
            seen.add(key)
            counts[key] = counts.get(key, 0) + 1
            first_rule.setdefault(key, rule)
    if not counts:
        return None
    best = max(counts, key=counts.get)  # max() keeps the first of equal counts
    return best, first_rule[best]


def parse_yes_no(text):
    """True for yes, False for no, None for not sure / skip, or "unclear" for anything else.

    Whole words only, so "nothing" is not "no". If a text has several answer words, the one that
    comes first wins, and of two words starting at the same place the longer one wins.
    """
    if not text:
        return "unclear"
    normalized = _normalize(text)
    best = None  # (start, -length, value)
    for phrase, value in _VOCAB:
        if _has_cjk(phrase):
            pattern = re.escape(phrase)  # Chinese has no spaces, so no word boundaries
        else:
            pattern = r"(?<!\w)" + re.escape(phrase) + r"(?!\w)"
        for match in re.finditer(pattern, normalized):
            candidate = (match.start(), -len(phrase), value)
            if best is None or candidate[:2] < best[:2]:
                best = candidate
    return "unclear" if best is None else best[2]


# Shown when the zip is unknown and the place only serves some zip codes.
ZIP_NOTE = {
    "en": "Serves zip codes: {zips}.",
    "es": "Atiende los códigos postales: {zips}.",
    "vi": "Phục vụ các mã bưu điện: {zips}.",
    "zh": "服务的邮编：{zips}。",
    "ar": "يخدم الرموز البريدية: {zips}.",
}

TERMINAL_MARKS = (".", "!", "?", "。", "！", "？", "؟", "…")


def _sentence(text):
    """The text with a full stop added when it has no end punctuation (CJK gets a 。)."""
    text = text.strip()
    if not text or text.endswith(TERMINAL_MARKS):
        return text
    return text + ("。" if _has_cjk(text) else ".")


def notes_for(place, answers=None, language="en", zip_code=None):
    """Notes to read out for a place: its "note" rules, plus a reminder for each rule the person skipped.

    Reminders need `answers`; without it only the note rules are returned. Every note ends with
    punctuation. When `zip_code` is None (the zip is unknown) a place with a zip_in rule also gets
    "Serves zip codes: ...".
    """
    lang = _lang(language)
    notes = []
    for rule in place.get("eligibility", []):
        kind = rule.get("type")
        if kind == "note":
            text = _sentence(rule.get("text", ""))
            if text:
                notes.append(text)
        elif kind == "zip_in" and zip_code is None and rule.get("zips"):
            zip_note = ZIP_NOTE[lang].format(zips=", ".join(str(z) for z in rule["zips"]))
            if zip_note not in notes:
                notes.append(zip_note)
        elif answers is not None and kind in REMINDERS:
            key = question_key(rule)
            if key is not None and key in answers and answers[key] is None:
                reminder = REMINDERS[kind][lang]
                if reminder not in notes:
                    notes.append(reminder)
    return notes
