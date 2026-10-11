"""Tests for eligibility.py. Uses small in-test rule lists, not the sample JSON file."""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import eligibility  # noqa: E402

LANGS = ["en", "es", "vi", "zh", "ar"]
ID = {"type": "id_required", "source_quote": "Please bring a photo ID."}
AGE_18_24 = {"type": "age", "min": 18, "max": 24, "source_quote": "Ages 18 to 24."}
AGE_MIN_18 = {"type": "age", "min": 18, "max": None, "source_quote": "18 and older."}
AGE_MAX_24 = {"type": "age", "min": None, "max": 24, "source_quote": "24 and younger."}
AGE_OPEN = {"type": "age", "min": None, "max": None, "source_quote": "All ages."}
WOMEN = {"type": "serves", "group": "women", "source_quote": "For women."}
FAMILIES = {"type": "serves", "group": "families", "source_quote": "For families."}
ZIP_77004 = {"type": "zip_in", "zips": ["77004", "77021"], "source_quote": "Serves 77004."}
HARRIS = {"type": "residency", "area": "Harris County", "source_quote": "Harris County residents."}
INCOME = {"type": "income_limit", "question": "Is your household income under $2,600 a month?",
          "source_quote": "Income under $2,600 a month."}
NOTE = {"type": "note", "text": "Appointment needed", "source_quote": "Appointments required."}


def place(place_id, *rules):
    """A small test place with the given eligibility rules."""
    return {"id": place_id, "name": place_id, "eligibility": list(rules)}


# question_key

def test_key_id_required():
    assert eligibility.question_key(ID) == "id"


def test_key_age_between():
    assert eligibility.question_key(AGE_18_24) == "age:18-24"


def test_key_age_min_only():
    assert eligibility.question_key(AGE_MIN_18) == "age:18-"


def test_key_age_max_only():
    assert eligibility.question_key(AGE_MAX_24) == "age:-24"


def test_key_age_without_limits_is_none():
    assert eligibility.question_key(AGE_OPEN) is None


def test_key_serves():
    assert eligibility.question_key(WOMEN) == "serves:women"


def test_key_residency():
    assert eligibility.question_key(HARRIS) == "residency:Harris County"


def test_key_income_uses_question_text():
    assert eligibility.question_key(INCOME) == "income:Is your household income under $2,600 a month?"


def test_key_note_is_none():
    assert eligibility.question_key(NOTE) is None


def test_key_zip_in_is_none():
    assert eligibility.question_key(ZIP_77004) is None


# question_text

@pytest.mark.parametrize("language, expected", [
    ("en", "Do you have a photo ID with you?"),
    ("es", "¿Tiene una identificación con foto?"),
    ("vi", "Bạn có mang theo giấy tờ tùy thân có ảnh không?"),
    ("zh", "您带了带照片的身份证件吗？"),
    ("ar", "هل معك بطاقة هوية بها صورة؟"),
])
def test_text_id_required_in_all_languages(language, expected):
    assert eligibility.question_text("id", ID, language) == expected


@pytest.mark.parametrize("language", LANGS)
def test_text_age_between_has_both_numbers(language):
    text = eligibility.question_text("age:18-24", AGE_18_24, language)
    assert "18" in text and "24" in text


def test_text_age_between_english():
    assert eligibility.question_text("age:18-24", AGE_18_24, "en") == "Are you between 18 and 24?"


def test_text_age_min_english():
    assert eligibility.question_text("age:18-", AGE_MIN_18, "en") == "Are you 18 or older?"


def test_text_age_max_english():
    assert eligibility.question_text("age:-24", AGE_MAX_24, "en") == "Are you 24 or younger?"


@pytest.mark.parametrize("language", LANGS)
def test_text_age_min_and_max_in_all_languages(language):
    assert "62" in eligibility.question_text("age:62-", {"type": "age", "min": 62}, language)
    assert "24" in eligibility.question_text("age:-24", AGE_MAX_24, language)


def test_text_age_without_limits_raises():
    with pytest.raises(ValueError):
        eligibility.question_text(None, AGE_OPEN, "en")


@pytest.mark.parametrize("group", ["women", "men", "families", "youth", "veterans", "seniors"])
@pytest.mark.parametrize("language", LANGS)
def test_text_serves_every_group_in_every_language(group, language):
    rule = {"type": "serves", "group": group}
    key = eligibility.question_key(rule)
    text = eligibility.question_text(key, rule, language)
    assert text
    if language != "en":
        assert text != eligibility.question_text(key, rule, "en")


def test_text_serves_women_english():
    assert eligibility.question_text("serves:women", WOMEN, "en") == \
        "Is this for a woman, or a mother with children?"


def test_text_serves_families_english():
    assert eligibility.question_text("serves:families", FAMILIES, "en") == "Do you have children with you?"


def test_text_residency_in_english_and_spanish():
    assert eligibility.question_text("residency:Harris County", HARRIS, "en") == "Do you live in Harris County?"
    assert eligibility.question_text("residency:Harris County", HARRIS, "es") == "¿Vive en Harris County?"


def test_text_income_english_is_the_source_question():
    key = eligibility.question_key(INCOME)
    assert eligibility.question_text(key, INCOME, "en") == INCOME["question"]


def test_text_income_translates_the_amount_in_spanish():
    key = eligibility.question_key(INCOME)
    text = eligibility.question_text(key, INCOME, "es")
    assert "2,600" in text and "¿" in text


def test_text_income_unusual_wording_stays_english():
    rule = {"type": "income_limit", "question": "Do you receive SNAP benefits?"}
    key = eligibility.question_key(rule)
    assert eligibility.question_text(key, rule, "vi") == "Do you receive SNAP benefits?"


def test_text_note_is_never_asked():
    with pytest.raises(ValueError):
        eligibility.question_text(None, NOTE, "en")


def test_text_unknown_language_falls_back_to_english():
    assert eligibility.question_text("id", ID, "fr") == "Do you have a photo ID with you?"


def test_text_key_must_match_rule():
    with pytest.raises(ValueError):
        eligibility.question_text("serves:men", WOMEN, "en")


# check

def test_check_unanswered_is_unknown():
    assert eligibility.check(ID, {}, None) is None


def test_check_yes_passes():
    assert eligibility.check(ID, {"id": True}, None) is True


def test_check_no_fails():
    assert eligibility.check(ID, {"id": False}, None) is False


def test_check_skipped_counts_as_pass():
    assert eligibility.check(ID, {"id": None}, None) is True


def test_check_age_answer_no_fails():
    assert eligibility.check(AGE_18_24, {"age:18-24": False}, None) is False


def test_check_age_answer_yes_passes():
    assert eligibility.check(AGE_18_24, {"age:18-24": True}, None) is True


def test_check_age_without_limits_needs_no_answer():
    assert eligibility.check(AGE_OPEN, {}, None) is True


def test_check_serves_yes_passes():
    assert eligibility.check(WOMEN, {"serves:women": True}, None) is True


def test_check_note_never_blocks():
    assert eligibility.check(NOTE, {}, "77002") is True


def test_check_zip_in_listed_zip_passes():
    assert eligibility.check(ZIP_77004, {}, "77004") is True


def test_check_zip_in_other_zip_fails():
    assert eligibility.check(ZIP_77004, {}, "77002") is False


def test_check_zip_in_unknown_zip_does_not_block():
    assert eligibility.check(ZIP_77004, {}, None) is True


def test_check_zip_in_zip_plus_four_uses_first_five_digits():
    assert eligibility.check(ZIP_77004, {}, "77004-1234") is True
    assert eligibility.check(ZIP_77004, {}, "77002-1234") is False


def test_check_zip_in_without_five_digits_does_not_block():
    assert eligibility.check(ZIP_77004, {}, "Third Ward") is True


def test_text_serves_youth_and_seniors_are_generic_english():
    assert eligibility.question_text("serves:youth", {"type": "serves", "group": "youth"}, "en") == \
        "Are you a young person (youth or young adult)?"
    assert eligibility.question_text("serves:seniors", {"type": "serves", "group": "seniors"}, "en") == \
        "Are you a senior (older adult)?"


# filter_places

def test_filter_drops_failing_places_and_keeps_order():
    places = [
        place("a", ID),
        place("b", ID, AGE_18_24),
        place("c"),
        place("d", FAMILIES),
    ]
    answers = {"id": True, "age:18-24": False, "serves:families": True}
    kept = eligibility.filter_places(places, answers, None)
    assert [p["id"] for p in kept] == ["a", "c", "d"]


def test_filter_keeps_places_with_unanswered_rules():
    places = [place("a", ID), place("b", WOMEN)]
    kept = eligibility.filter_places(places, {"id": True}, None)
    assert [p["id"] for p in kept] == ["a", "b"]


def test_filter_uses_zip_code():
    places = [place("in", ZIP_77004), place("any", ID), place("out", ZIP_77004)]
    kept = eligibility.filter_places(places, {"id": True}, "77004")
    assert [p["id"] for p in kept] == ["in", "any", "out"]
    kept_other = eligibility.filter_places(places, {"id": True}, "77002")
    assert [p["id"] for p in kept_other] == ["any"]


def test_filter_empty_list():
    assert eligibility.filter_places([], {}, None) == []


# next_question

def test_next_question_picks_most_common_among_first_three():
    places = [
        place("a", ID),
        place("b", FAMILIES, ID),
        place("c", ID, WOMEN),
        place("d", WOMEN, WOMEN),  # fourth place, ignored
        place("e", WOMEN, WOMEN),
    ]
    key, rule = eligibility.next_question(places, {}, None, 0)
    assert key == "id"
    assert rule == ID


def test_next_question_tie_goes_to_first_appearance():
    places = [place("a", FAMILIES), place("b", WOMEN)]
    key, rule = eligibility.next_question(places, {}, None, 0)
    assert key == "serves:families"
    assert rule == FAMILIES


def test_next_question_ignores_places_after_the_first_three():
    places = [place("a"), place("b"), place("c"), place("d", ID), place("e", ID)]
    assert eligibility.next_question(places, {}, None, 0) is None


def test_next_question_skips_answered_keys():
    places = [place("a", ID, WOMEN), place("b", ID, WOMEN)]
    key, _rule = eligibility.next_question(places, {"id": True}, None, 1)
    assert key == "serves:women"


def test_next_question_skipped_key_is_not_asked_again():
    places = [place("a", ID)]
    assert eligibility.next_question(places, {"id": None}, None, 1) is None


def test_next_question_stops_at_max_questions():
    places = [place("a", ID)]
    assert eligibility.next_question(places, {}, None, eligibility.MAX_QUESTIONS) is None


def test_next_question_never_returns_zip_or_note():
    places = [place("a", ZIP_77004, NOTE)]
    assert eligibility.next_question(places, {}, None, 0) is None


def test_next_question_no_places():
    assert eligibility.next_question([], {}, None, 0) is None


# parse_yes_no

@pytest.mark.parametrize("text", [
    "yes", "Yes", "yeah", "yep", "y", "ok",          # en
    "sí", "Sí", "Si", "si", "claro",                  # es
    "có", "vâng", "dạ",                                # vi
    "是", "是的", "对", "有",                           # zh
    "نعم", "أجل",                                      # ar
])
def test_yes_words_in_all_languages(text):
    assert eligibility.parse_yes_no(text) is True


@pytest.mark.parametrize("text", [
    "no", "No", "nope", "n",                          # en
    "no tengo", "No",                                 # es
    "không", "khong",                                 # vi
    "不", "没有", "不是",                              # zh
    "لا", "كلا",                                      # ar
])
def test_no_words_in_all_languages(text):
    assert eligibility.parse_yes_no(text) is False


@pytest.mark.parametrize("text", [
    "skip", "not sure", "don't know", "idk",          # en
    "no sé", "omitir", "no lo sé",                    # es
    "không biết", "bỏ qua",                           # vi
    "不知道", "跳过",                                   # zh
    "لا أعرف", "تخطي",                                 # ar
])
def test_skip_phrases_in_all_languages(text):
    assert eligibility.parse_yes_no(text) is None


def test_accents_do_not_matter():
    assert eligibility.parse_yes_no("SÍ") is True
    assert eligibility.parse_yes_no("sí.") is True
    assert eligibility.parse_yes_no("Si") is True
    assert eligibility.parse_yes_no("KHÔNG") is False


def test_nothing_is_not_no():
    assert eligibility.parse_yes_no("nothing") == "unclear"


def test_yesterday_is_not_yes():
    assert eligibility.parse_yes_no("yesterday") == "unclear"


def test_no_se_is_skip_not_no():
    assert eligibility.parse_yes_no("no sé") is None
    assert eligibility.parse_yes_no("no") is False


def test_sentence_with_yes_word():
    assert eligibility.parse_yes_no("Yes, I have one") is True


def test_sentence_with_no_word():
    assert eligibility.parse_yes_no("No, I don't have one") is False


def test_skip_word_inside_sentence():
    assert eligibility.parse_yes_no("I'm not sure") is None


@pytest.mark.parametrize("text", ["Where is the bus stop?", "what time is it", "¿dónde queda?"])
def test_other_text_is_unclear(text):
    assert eligibility.parse_yes_no(text) == "unclear"


def test_empty_text_is_unclear():
    assert eligibility.parse_yes_no("") == "unclear"
    assert eligibility.parse_yes_no(None) == "unclear"


# notes_for

def test_notes_returns_note_rule_texts():
    assert eligibility.notes_for(place("a", NOTE)) == ["Appointment needed."]


def test_notes_empty_when_no_rules():
    assert eligibility.notes_for(place("a")) == []


def test_notes_reminder_for_skipped_rule():
    p = place("a", ID)
    assert eligibility.notes_for(p, {"id": None}) == ["Bring a photo ID if you have one."]


def test_notes_no_reminder_when_answered_yes():
    assert eligibility.notes_for(place("a", ID), {"id": True}) == []


def test_notes_no_reminder_without_answers():
    assert eligibility.notes_for(place("a", ID, NOTE)) == ["Appointment needed."]


def test_notes_reminder_in_spanish():
    p = place("a", ID)
    assert eligibility.notes_for(p, {"id": None}, "es") == ["Traiga una identificación con foto si tiene una."]


def test_notes_reminders_are_not_repeated():
    p = place("a", FAMILIES, WOMEN)  # both are "serves", so the reminder is the same text
    answers = {"serves:families": None, "serves:women": None}
    assert eligibility.notes_for(p, answers) == ["Ask staff if this applies to you."]


def test_notes_skipped_zip_or_note_gives_no_reminder():
    p = place("a", ZIP_77004, NOTE)
    assert eligibility.notes_for(p, {}, zip_code="77004") == ["Appointment needed."]


def test_youth_rule_is_not_asked_after_the_age_question():
    # "Are you between 18 and 24?" then "Are you a young person?" asks the same thing twice.
    p = {"id": "c", "eligibility": [{"type": "age", "min": 18, "max": 24, "source_quote": "q"},
                                    {"type": "serves", "group": "youth", "source_quote": "q"}]}
    key, _rule = eligibility.next_question([p], {}, None, 0)
    assert key == "age:18-24"
    assert eligibility.next_question([p], {key: True}, None, 1) is None
    assert eligibility.filter_places([p], {key: True}, None) == [p]
    assert eligibility.filter_places([p], {key: False}, None) == []


def test_youth_rule_is_asked_when_there_is_no_age_rule():
    p = {"id": "y", "eligibility": [{"type": "serves", "group": "youth", "source_quote": "q"}]}
    assert eligibility.next_question([p], {}, None, 0)[0] == "serves:youth"
