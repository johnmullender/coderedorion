"""Tests for brain.py. These use the fallback only: the Gemini client is
replaced with None, so no network or API key is needed."""

import os
import sys

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
    assert "Reply 2 or 3" in reply


def test_sms_single_match_has_no_reply_hint():
    reply = brain.answer("I am hungry", "en", "food", [make_match("Pantry")], "sms")
    assert "Pantry" in reply
    assert "Reply 2 or 3" not in reply
    assert "6 PM" in reply


def test_sms_spanish_template():
    reply = brain.answer("tengo hambre", "es", "food",
                         [make_match("Despensa"), make_match("Otra")], "sms")
    assert "Despensa" in reply
    assert "Responda 2 o 3" in reply
    assert len(reply) <= 300


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
    assert brain._check_gemini_reply("Pantry is open now 😊", "kiosk", matches, "en") is None
    assert brain._check_gemini_reply("Pantry is open now ☀", "kiosk", matches, "en") is None
    assert brain._check_gemini_reply("x" * 351, "kiosk", matches, "en") is None
    ok = "Pantry is open now, 0.9 miles away."
    assert brain._check_gemini_reply(ok, "kiosk", matches, "en") == ok


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
    assert word in sms and brain.REPLY_HINTS[language] in sms
    nothing = brain.answer("food", language, "food", [], "kiosk")
    assert "211" in nothing and "Please call" not in nothing


def test_vietnamese_shelter_words_without_gemini(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(brain, "_client", None)
    result = brain.understand("Tôi cần nơi trú ẩn.", [], "vi")
    assert result["need"] == "shelter" and result["intent"] == "find"
