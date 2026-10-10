"""Tests for voice.py. httpx.post is replaced with fakes, so no network is used."""

import sys
import pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import voice  # noqa: E402


class FakeResponse:
    """A stand-in for the object httpx.post returns."""

    def __init__(self, status_code=200, content=b"", json_data=None, text=""):
        self.status_code = status_code
        self.content = content
        self._json = json_data
        self.text = text or (content.decode("utf-8", "ignore") if content else "")

    def json(self):
        return self._json


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Start every test with a known environment and an empty cache."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
    monkeypatch.delenv("ELEVENLABS_TTS_MODEL", raising=False)
    monkeypatch.delenv("ELEVENLABS_STT_MODEL", raising=False)
    voice._tts_cache.clear()
    yield
    voice._tts_cache.clear()


def fake_post(monkeypatch, response=None, error=None):
    """Replace httpx.post. Records every call in the returned list."""
    calls = []

    def _post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        if error is not None:
            raise error
        return response

    monkeypatch.setattr(voice.httpx, "post", _post)
    return calls


def test_disabled_without_key_returns_none(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    calls = fake_post(monkeypatch, FakeResponse(content=b"mp3"))

    assert voice.enabled() is False
    assert voice.tts("hello", "en") is None
    assert voice.stt(b"audio", "a.webm", "audio/webm", "en") is None
    assert calls == []  # httpx was never called


def test_tts_success_returns_bytes_and_sends_right_request(monkeypatch):
    calls = fake_post(monkeypatch, FakeResponse(status_code=200, content=b"MP3DATA"))

    audio = voice.tts("  Hola, bienvenido  ", "es")

    assert audio == b"MP3DATA"
    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == "https://api.elevenlabs.io/v1/text-to-speech/JBFqnCBsd6RMkjVDRZzb"
    assert call["params"] == {"output_format": "mp3_44100_128"}
    assert call["headers"]["xi-api-key"] == "test-key"
    assert call["headers"]["Accept"] == "audio/mpeg"
    assert call["json"] == {
        "text": "Hola, bienvenido",
        "model_id": "eleven_flash_v2_5",
        "language_code": "es",
    }
    assert call["timeout"] == 15


def test_tts_uses_env_voice_and_model(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice123")
    monkeypatch.setenv("ELEVENLABS_TTS_MODEL", "model-x")
    calls = fake_post(monkeypatch, FakeResponse(content=b"x"))

    voice.tts("hi", "en")

    assert calls[0]["url"].endswith("/voice123")
    assert calls[0]["json"]["model_id"] == "model-x"


def test_tts_caches_success(monkeypatch):
    calls = fake_post(monkeypatch, FakeResponse(content=b"MP3"))

    first = voice.tts("same text", "en")
    second = voice.tts("same text", "en")

    assert first == second == b"MP3"
    assert len(calls) == 1  # second call came from the cache


def test_tts_empty_text_returns_none_without_calling(monkeypatch):
    calls = fake_post(monkeypatch, FakeResponse(content=b"MP3"))

    assert voice.tts("   ", "en") is None
    assert calls == []


def test_tts_non_200_returns_none(monkeypatch, capsys):
    fake_post(monkeypatch, FakeResponse(status_code=401, text="invalid key"))

    assert voice.tts("hello", "en") is None
    assert "401" in capsys.readouterr().out


def test_tts_exception_returns_none(monkeypatch):
    fake_post(monkeypatch, error=RuntimeError("network down"))

    assert voice.tts("hello", "en") is None


def test_stt_success_maps_language_code(monkeypatch):
    calls = fake_post(
        monkeypatch,
        FakeResponse(json_data={"text": "  necesito comida  ", "language_code": "spa"}),
    )

    result = voice.stt(b"audio-bytes", "clip.webm", "audio/webm", None)

    assert result == {"text": "necesito comida", "language": "es"}
    call = calls[0]
    assert call["url"] == "https://api.elevenlabs.io/v1/speech-to-text"
    assert call["headers"] == {"xi-api-key": "test-key"}
    assert call["data"] == {"model_id": "scribe_v2"}
    assert call["files"]["file"] == ("clip.webm", b"audio-bytes", "audio/webm")
    assert call["timeout"] == 15


def test_stt_passes_language_code_only_when_lang_given(monkeypatch):
    calls = fake_post(monkeypatch, FakeResponse(json_data={"text": "hi", "language_code": "eng"}))

    voice.stt(b"a", "", "", "vi")
    assert calls[-1]["data"] == {"model_id": "scribe_v2", "language_code": "vi"}
    assert calls[-1]["files"]["file"][0] == "audio.webm"
    assert calls[-1]["files"]["file"][2] == "audio/webm"

    voice.stt(b"a", "x.webm", "audio/webm", None)
    assert "language_code" not in calls[-1]["data"]


def test_stt_unknown_language_becomes_en(monkeypatch):
    fake_post(monkeypatch, FakeResponse(json_data={"text": "hello", "language_code": "fra"}))

    assert voice.stt(b"a", "x.webm", "audio/webm", None)["language"] == "en"


def test_stt_empty_text_returns_none(monkeypatch):
    fake_post(monkeypatch, FakeResponse(json_data={"text": "   ", "language_code": "eng"}))

    assert voice.stt(b"a", "x.webm", "audio/webm", "en") is None


def test_stt_error_returns_none(monkeypatch):
    fake_post(monkeypatch, FakeResponse(status_code=500, text="server error"))
    assert voice.stt(b"a", "x.webm", "audio/webm", "en") is None

    fake_post(monkeypatch, error=ValueError("boom"))
    assert voice.stt(b"a", "x.webm", "audio/webm", "en") is None


def test_stt_empty_audio_returns_none_without_calling(monkeypatch):
    calls = fake_post(monkeypatch, FakeResponse(json_data={"text": "hi"}))

    assert voice.stt(b"", "x.webm", "audio/webm", "en") is None
    assert calls == []
