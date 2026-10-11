"""Tests for brain.py. These use the fallback only: the Gemini client is
replaced with None, so no network or API key is needed."""

import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_ROOT)

import brain  # noqa: E402


@pytest.fixture(autouse=True)
def no_gemini(monkeypatch):
    """Make sure no test ever uses the Gemini client."""
    monkeypatch.setattr(brain, "_get_client", lambda: None)


def make_match(name, address="100 Main St, Houston, TX 77002", distance=0.9,
               closes="18:00", phone="713-555-0100"):
    return {
        "id": name.lower().replace(" ", "-"),
        "name": name,
        "address": address,
        "distance_mi": distance,
        "open_now": True,
        "closes_at": closes,
        "opens_at": None,
        "requirements": {"id_required": False},
        "phone": phone,
        "notes": "",
        "hours": {"mon": ["09:00-17:00"]},
    }


# ---------------------------------------------------------------------------
# understand(): needs in all 5 languages
# ---------------------------------------------------------------------------

NEED_CASES = [
    ("I am hungry", "food"),
    ("tengo hambre", "food"),
    ("tôi đói và cần đồ ăn", "food"),
    ("我很饿", "food"),
    ("أنا جائع", "food"),
    ("I need a place to sleep", "shelter"),
    ("necesito un albergue", "shelter"),
    ("tôi cần chỗ ở", "shelter"),
    ("我需要住的地方", "shelter"),
    ("أحتاج مأوى", "shelter"),
    ("where is a shower", "hygiene"),
    ("necesito una ducha", "hygiene"),
    ("tôi cần tắm", "hygiene"),
    ("哪里可以洗澡", "hygiene"),
    ("أين يمكنني الاستحمام", "hygiene"),
    ("I need a doctor", "clinic"),
    ("necesito un médico", "clinic"),
    ("tôi cần bác sĩ", "clinic"),
    ("我需要看病", "clinic"),
    ("أريد طبيب", "clinic"),
    ("I need wifi at a library", "library"),
    ("quiero una biblioteca", "library"),
    ("tôi cần thư viện", "library"),
    ("图书馆在哪里", "library"),
    ("أين المكتبة", "library"),
    ("it is too hot", "cooling"),
    ("hace mucho calor", "cooling"),
    ("trời nóng quá", "cooling"),
    ("太热了", "cooling"),
    ("الجو حار", "cooling"),
    ("I need a lawyer", "legal"),
    ("necesito un abogado", "legal"),
    ("tôi cần luật sư", "legal"),
    ("我需要律师", "legal"),
    ("أحتاج محامي", "legal"),
]


@pytest.mark.parametrize("text,need", NEED_CASES)
def test_need_detected_in_every_language(text, need):
    result = brain.understand(text, [])
    assert result["intent"] == "find"
    assert result["need"] == need


def test_understand_returns_exactly_five_keys():
    result = brain.understand("I am hungry", [])
    assert set(result) == {"intent", "need", "language", "location_text", "option_number"}


# ---------------------------------------------------------------------------
# understand(): options, help, followups, languages, hints
# ---------------------------------------------------------------------------

def test_bare_number_is_next_option():
    result = brain.understand("2", [])
    assert result["intent"] == "next_option"
    assert result["option_number"] == 2
    assert result["need"] is None


def test_option_word_is_next_option():
    result = brain.understand("option 3", [])
    assert result["intent"] == "next_option"
    assert result["option_number"] == 3


def test_ayuda_is_help():
    result = brain.understand("AYUDA", [])
    assert result["intent"] == "help"
    assert result["need"] is None


def test_help_word_is_help():
    assert brain.understand("HELP", [])["intent"] == "help"


def test_followup_question_without_need():
    result = brain.understand("is it open tomorrow?", [])
    assert result["intent"] == "followup"
    assert result["need"] is None


def test_lang_hint_forces_language():
    result = brain.understand("food", [], lang_hint="vi")
    assert result["language"] == "vi"
    assert result["need"] == "food"


def test_language_detected_from_script():
    assert brain.understand("我很饿", [])["language"] == "zh"
    assert brain.understand("أنا جائع", [])["language"] == "ar"
    assert brain.understand("tôi đói", [])["language"] == "vi"
    assert brain.understand("tengo hambre", [])["language"] == "es"
    assert brain.understand("I am hungry", [])["language"] == "en"


def test_location_text_after_near():
    result = brain.understand("food near UH", [])
    assert result["location_text"] == "UH"
    assert result["need"] == "food"


def test_location_text_is_zip():
    result = brain.understand("77004", [])
    assert result["location_text"] == "77004"


# ---------------------------------------------------------------------------
# answer()
# ---------------------------------------------------------------------------

def test_sms_with_three_long_matches_fits_300_chars():
    matches = [
        make_match("A" * 120, address="B" * 200 + " Houston, TX 77004", distance=0.4),
        make_match("C" * 120, address="D" * 200, distance=1.2),
        make_match("E" * 120, address="F" * 200, distance=2.5),
    ]
    reply = brain.answer("I am hungry", "en", "food", matches, "sms")
    assert len(reply) <= 300
    assert "BBB" not in reply and "Reply" not in reply  # no address, and the hint comes from main.py


def test_sms_single_match_is_one_sentence_without_address_or_hint():
    reply = brain.answer("I am hungry", "en", "food", [make_match("Pantry")], "sms")
    assert reply == "Pantry - open now, 0.9 mi away, closes 6 PM."


def test_sms_spanish_template():
    reply = brain.answer("tengo hambre", "es", "food",
                         [make_match("Despensa"), make_match("Otra")], "sms")
    assert reply == "Despensa: abierto ahora, a 0.9 mi, cierra a las 6 PM."


def test_kiosk_answer_has_no_links():
    reply = brain.answer("I am hungry", "en", "food", [make_match("Pantry")], "kiosk")
    assert "http" not in reply.lower()
    assert "Pantry" in reply


def test_no_matches_with_unmet_next_names_next_place():
    next_place = make_match("Sunrise Shelter", distance=3.1, closes=None)
    next_place["opens_at"] = "Mon 09:00"
    reply = brain.answer("I need a bed", "en", "shelter", [], "sms", unmet_next=next_place)
    assert "Sunrise Shelter" in reply
    assert "9 AM" in reply
    assert "211" not in reply


def test_no_matches_without_unmet_next_mentions_211():
    reply = brain.answer("I need a bed", "en", "shelter", [], "sms", unmet_next=None)
    assert "211" in reply


def test_answer_never_raises_on_bad_input():
    reply = brain.answer("x", "en", None, [{"name": "Only a name"}], "sms")
    assert isinstance(reply, str)
    assert reply


# ---------------------------------------------------------------------------
# Reviewer fixes
# ---------------------------------------------------------------------------

def test_vietnamese_an_inside_other_words_is_not_food():
    # "khăn" (towel) and "chăn" (blanket) contain "ăn" but are not food.
    assert brain.understand("tôi cần khăn", [])["need"] != "food"
    assert brain.understand("tôi cần chăn", [])["need"] != "food"
    # The whole word "ăn" still means food.
    assert brain.understand("tôi muốn ăn", [])["need"] == "food"


@pytest.mark.parametrize("text,expected", [
    ("necesito comida en la noche", None),
    ("eat at 5pm", None),
    ("cerca del UH hoy", "del UH"),
    ("food near UH tonight", "UH"),
    ("food by tomorrow", None),
])
def test_location_text_ignores_times(text, expected):
    assert brain.understand(text, [])["location_text"] == expected


def test_kiosk_gemini_reply_rejects_emoji_and_long_text():
    matches = [make_match("Pantry")]
    assert brain._check_gemini_reply("Pantry is open now 😊", "kiosk", matches) is None
    assert brain._check_gemini_reply("Pantry is open now ☀", "kiosk", matches) is None
    assert brain._check_gemini_reply("x" * 351, "kiosk", matches) is None
    ok = "Pantry is open now, 0.9 miles away."
    assert brain._check_gemini_reply(ok, "kiosk", matches) == ok


def test_gemini_reply_with_address_or_phone_is_rejected():
    matches = [make_match("Pantry")]
    assert brain._check_gemini_reply("Pantry is at 100 Main St, Houston, TX 77002.", "kiosk", matches) is None
    assert brain._check_gemini_reply("Pantry is open, call 713-555-0100.", "sms", matches) is None
    hours_unknown = [dict(make_match("Pantry"), hours_unknown=True)]
    said = "Pantry, 0.9 miles away, hours not listed, call 713-555-0100 first."
    assert brain._check_gemini_reply(said, "kiosk", hours_unknown) == said


def test_sms_unmet_next_with_long_fields_fits_300_chars():
    next_place = make_match("S" * 200, address="A" * 250, closes=None,
                            phone="713-555-0199")
    next_place["opens_at"] = "Tue 07:30"
    for language in ["en", "es", "vi"]:
        reply = brain.answer("bed", language, "shelter", [], "sms", unmet_next=next_place)
        assert len(reply) <= 300
        assert "SSSS" in reply


def test_number_only_message_uses_language_from_history():
    history = [
        {"role": "user", "text": "tengo hambre"},
        {"role": "bot", "text": "Despensa: abierto ahora."},
    ]
    result = brain.understand("2", history)
    assert result["intent"] == "next_option"
    assert result["language"] == "es"
    # With no history, it stays English.
    assert brain.understand("2", [])["language"] == "en"


@pytest.mark.parametrize("text,language", [
    ("I need a bath", "en"),
    ("quiero bañarme", "es"),
    ("necesito ducharme", "es"),
    ("dónde me puedo bañar", "es"),
])
def test_new_hygiene_words(text, language):
    result = brain.understand(text, [])
    assert result["need"] == "hygiene"
    assert result["language"] == language


@pytest.mark.parametrize("language,word", [("vi", "đang mở cửa"), ("zh", "现在开放"), ("ar", "مفتوح الآن")])
def test_templates_without_gemini_use_the_persons_language(language, word, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(brain, "_client", None)
    matches = [make_match("Sample Pantry"), make_match("Sample Kitchen")]
    kiosk = brain.answer("food", language, "food", matches, "kiosk")
    sms = brain.answer("food", language, "food", matches, "sms")
    assert word in kiosk and "Sample Pantry" in kiosk
    assert word in sms and "Sample Pantry" in sms
    assert "713-555-0100" not in kiosk and "100 Main St" not in sms
    nothing = brain.answer("food", language, "food", [], "kiosk")
    assert "211" in nothing and "Please call" not in nothing


# ---------------------------------------------------------------------------
# understand(): directions (fallback only, no Gemini key)
# ---------------------------------------------------------------------------

@pytest.fixture
def fallback_only(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(brain, "_client", None)


@pytest.mark.parametrize("text", [
    "how do I get there",
    "how can I get there?",
    "which bus should I take",
    "¿cómo llego?",
    "qué autobús tomo",
    "đi bằng xe buýt nào",
    "làm sao để đến đó",
    "怎么去",
    "坐哪路公交",
    "كيف أصل",
    "أي حافلة",
])
def test_directions_intent_in_every_language(text, fallback_only):
    result = brain.understand(text, [])
    assert result["intent"] == "directions"
    assert result["need"] is None


def test_where_can_i_get_food_is_find_not_directions(fallback_only):
    result = brain.understand("where can I get food", [])
    assert result["intent"] == "find"
    assert result["need"] == "food"


def test_directions_with_need_stays_find(fallback_only):
    assert brain.understand("how do I get to the food pantry", [])["intent"] == "find"


def test_directions_is_in_intents_list():
    assert "directions" in brain.INTENTS


def test_plain_followup_still_followup(fallback_only):
    assert brain.understand("is it far?", [])["intent"] == "followup"


# ---------------------------------------------------------------- offers: labels, line, Gemini facts

LANGS = ["en", "es", "vi", "zh", "ar"]


def place_with_offers(*keys):
    """A place whose offers are the given keys (quotes and urls are placeholders)."""
    return {"name": "Test Place", "offers": [
        {"key": key, "quote": "a quote", "url": "https://example.org/offers"} for key in keys]}


def test_every_offer_key_has_a_label_in_every_language():
    for language in LANGS:
        labels = brain.OFFER_LABELS[language]
        assert set(labels) == set(brain.OFFER_KEYS), language
        assert all(label.strip() for label in labels.values()), language
    assert len(brain.OFFER_KEYS) == len(set(brain.OFFER_KEYS)) == 24


def test_every_language_has_an_offers_lead_word():
    for language in LANGS:
        assert "{items}" in brain.TEMPLATES[language]["offers"], language
        assert brain.TEMPLATES[language]["offers_sep"].strip(), language


def test_offers_line_examples():
    place = place_with_offers("showers", "wifi", "food_pantry")
    assert brain.offers_line(place, "en") == "Offers: showers, Wi-Fi, food pantry."
    assert brain.offers_line(place, "es") == "Ofrece: duchas, Wi-Fi, despensa de comida."
    assert brain.offers_line(place, "zh") == "提供：洗澡、无线网络、食品库。"
    assert brain.offers_line(place, "ar").startswith("تقدم: ")


def test_offers_line_keeps_four_in_data_order_and_skips_bad_entries():
    place = place_with_offers("laundry", "bogus", "mail", "showers", "wifi", "restrooms")
    place["offers"].append("not an object")
    assert brain.offers_line(place, "en") == "Offers: laundry, mail, showers, Wi-Fi."


def test_offers_line_is_none_without_offers():
    assert brain.offers_line({"name": "x"}, "en") is None
    assert brain.offers_line({"offers": []}, "en") is None
    assert brain.offers_line(place_with_offers("bogus"), "en") is None


def test_more_options_hint_is_translated_for_each_channel():
    for language in ("en", "es", "vi", "zh", "ar"):
        sms = brain.more_options_hint(language, "sms", 2)
        kiosk = brain.more_options_hint(language, "kiosk", 2)
        assert "2" in sms and kiosk and sms != kiosk, language
        assert "3" in brain.more_options_hint(language, "sms", 3), language
    assert brain.more_options_hint("en", "sms", 3) == "Reply 3 for more options."
    assert brain.more_options_hint("en", "kiosk", 3) == "Turn the knob for more options."


def test_facts_send_offer_labels_but_not_notes_or_links():
    match = dict(place_with_offers("showers", "wifi"), name="Test Place", notes="Internal remark",
                 address="1 Main", distance_mi=0.2, open_now=True, phone="713-555-0100")
    facts = brain._facts_for(match, 1, "es")
    assert facts["offers"] == ["duchas", "Wi-Fi"]
    assert "notes" not in facts
    assert "Internal remark" not in str(facts)
    assert "example.org" not in str(facts) and "a quote" not in str(facts)


def test_notes_are_not_a_fact_and_prompt_forbids_unlisted_services():
    assert "notes" not in brain.FACT_FIELDS
    assert "offers" in brain.FACT_FIELDS
    prompt = " ".join(brain.ANSWER_PROMPT.split())  # the rule is wrapped over two lines in the source
    assert 'Describe a place only with the offers listed in its "offers" field.' in prompt
    assert "Never suggest a service (beds, meals, etc.) that is not listed" in prompt


class FakeGemini:
    """Stands in for the Gemini client: records the prompt and returns a fixed reply."""

    def __init__(self, reply="Pantry is open now."):
        self.prompts = []
        self.reply = reply
        self.models = self

    def generate_content(self, model, contents, config):
        self.prompts.append(contents)

        class Reply:
            text = self.reply
        return Reply()


def test_gemini_prompt_gets_offer_labels_and_no_notes():
    client = FakeGemini()
    match = dict(place_with_offers("hot_meals", "shelter_beds"), name="Test Place", notes="Call to see if you can get a bed",
                 address="1 Main", distance_mi=0.2, open_now=True, phone="713-555-0100")
    reply = brain._answer_with_gemini(client, "I need food", "en", "food", [match], "kiosk", None, [])
    assert reply == "Pantry is open now."
    prompt = client.prompts[0]
    assert '"free meals"' in prompt and '"shelter beds"' in prompt
    assert "Call to see if you can get a bed" not in prompt
    assert "example.org" not in prompt


def test_gemini_answer_prompt_has_the_short_shape_rules():
    prompt = " ".join(brain.ANSWER_PROMPT.split())
    assert "Never say to call a place to confirm its hours." in prompt
    assert "Never give an address, a phone number, eligibility notes, or bus or walking directions." in prompt
    assert "Reply 2" not in brain.ANSWER_PROMPT and "Turn the knob" not in brain.ANSWER_PROMPT


def test_gemini_answer_prompt_carries_the_offers_rule(monkeypatch):
    client = FakeGemini(reply="Test Place is open now, 0.2 miles away.")
    monkeypatch.setattr(brain, "_get_client", lambda: client)
    text = brain.answer("I need food", "en", "food", [{"name": "Test Place", "distance_mi": 0.2,
                                                       "open_now": True, "closes_at": None}], "kiosk")
    assert text == "Test Place is open now, 0.2 miles away."
    assert "Describe a place only with the offers listed" in " ".join(client.prompts[0].split())


# ---------------------------------------------------------------- opening times: day names, today, tomorrow

def houston(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("America/Chicago"))


MONDAY_10AM = houston(2026, 10, 12, 10, 0)  # 2026-10-12 is a Monday


def test_opening_day_names_are_translated():
    assert brain._opens_text("Wed 09:00", "es", now=MONDAY_10AM) == "Mié 9 AM"
    assert brain._opens_text("Fri 13:00", "vi", now=MONDAY_10AM) == "T6 1 PM"
    assert brain._opens_text("Sun 09:00", "zh", now=MONDAY_10AM) == "周日 9 AM"
    assert brain._opens_text("Sat 09:00", "ar", now=MONDAY_10AM) == "السبت 9 AM"
    assert brain._opens_text("Wed 09:00", "en", now=MONDAY_10AM) == "Wed 9 AM"


def test_today_and_tomorrow_are_said_in_the_language():
    assert brain._opens_text("Mon 14:00", "en", now=MONDAY_10AM) == "today 2 PM"  # later today
    assert brain._opens_text("Tue 09:00", "en", now=MONDAY_10AM) == "tomorrow 9 AM"
    assert brain._opens_text("Mon 14:00", "es", now=MONDAY_10AM) == "hoy 2 PM"
    assert brain._opens_text("Tue 09:00", "es", now=MONDAY_10AM) == "mañana 9 AM"
    assert brain._opens_text("Tue 09:00", "vi", now=MONDAY_10AM) == "ngày mai 9 AM"
    assert brain._opens_text("Mon 14:00", "zh", now=MONDAY_10AM) == "今天 2 PM"
    assert brain._opens_text("Tue 09:00", "ar", now=MONDAY_10AM) == "غدا 9 AM"


def test_monday_morning_already_passed_is_not_today():
    # Monday 09:00 when it is already 10 AM on Monday: the next one is a week away, so use the day name.
    assert brain._opens_text("Mon 09:00", "en", now=MONDAY_10AM) == "Mon 9 AM"
    assert brain._opens_text("Mon 09:00", "es", now=MONDAY_10AM) == "Lun 9 AM"


def test_opening_text_uses_houston_time_when_no_time_is_given():
    text = brain._opens_text("Mon 09:00", "en")
    assert text.endswith(" 9 AM") and text.split(" ")[0] in ("today", "tomorrow", "Mon", "Tue", "Wed", "Thu",
                                                             "Fri", "Sat", "Sun")


def test_next_open_text_is_translated_and_has_distance_only():
    place = {"name": "Later Place", "address": "2 Main St", "opens_at": "Tue 09:00", "phone": "713-555-0100",
             "distance_mi": 0.7}
    text = brain.next_open_text("es", place)
    assert "Tue" not in text  # translated: hoy, mañana, or Mar
    assert "abre" in text
    assert text.endswith(", a 0.7 millas.")
    assert "2 Main St" not in text and "713-555-0100" not in text


def test_next_open_place_is_never_described_as_open_now():
    place = {"name": "Later Place", "address": "2 Main St", "distance_mi": 0.7, "open_now": False,
             "closes_at": None, "opens_at": "Tue 09:00", "phone": "713-555-0100"}
    sms = brain.answer("food", "en", "food", [place], "sms")
    kiosk = brain.answer("food", "en", "food", [place], "kiosk")
    for text in (sms, kiosk):
        assert "The next one to open is Later Place" in text
        assert "open now" not in text.lower()
