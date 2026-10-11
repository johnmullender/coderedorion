"""Tests for telegram_bot.answer: Telegram messages get the same replies as SMS."""

import telegram_bot


def test_text_message_uses_the_sms_flow(monkeypatch):
    calls = []
    monkeypatch.setattr(telegram_bot.main, "handle_ask",
                        lambda channel, session_id, text, **kw: calls.append((channel, session_id, text, kw)) or {"reply": "ok"})
    assert telegram_bot.answer({"chat": {"id": 42}, "text": "i need food"}) == "ok"
    assert calls == [("sms", "telegram:42", "i need food", {})]


def test_shared_location_is_passed_as_coordinates(monkeypatch):
    calls = []
    monkeypatch.setattr(telegram_bot.main, "handle_ask",
                        lambda channel, session_id, text, **kw: calls.append(kw) or {"reply": "ok"})
    telegram_bot.answer({"chat": {"id": 42}, "location": {"latitude": 29.75, "longitude": -95.36}})
    assert calls == [{"lat": 29.75, "lng": -95.36}]


def test_messages_without_text_get_no_reply():
    assert telegram_bot.answer({"chat": {"id": 42}, "sticker": {}}) is None


def test_shared_location_answers_where_are_you():
    import main
    main.SESSIONS.clear()
    first = telegram_bot.answer({"chat": {"id": 7}, "text": "i need food"})
    assert "zip" in first.lower()
    second = telegram_bot.answer({"chat": {"id": 7}, "location": {"latitude": 29.7522, "longitude": -95.3584}})
    assert "zip" not in second.lower() and "HelpLine Houston" not in second
