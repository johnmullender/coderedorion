"""ElevenLabs voice helpers for the HelpLine kiosk.

- tts(text, lang): text -> MP3 bytes (the kiosk plays them in the browser)
- stt(audio, ...):  recorded audio -> transcript text + language

The API key stays on the server. The kiosk never talks to ElevenLabs
directly; it calls our /tts and /stt routes, which call these functions.

Every public function returns None instead of raising, so a voice problem
never breaks the kiosk. The kiosk then falls back to browser speech.
"""

import os
from collections import OrderedDict

import httpx
from dotenv import load_dotenv

# Read the .env file (if there is one) so the API key is available.
load_dotenv()

TTS_URL_BASE = "https://api.elevenlabs.io/v1/text-to-speech/"
STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"

MAX_TTS_CHARS = 1000   # longer text is cut down to this many characters
CACHE_SIZE = 100       # how many TTS results to remember
TIMEOUT_SECONDS = 15

# ElevenLabs sometimes returns 3-letter language codes. Map them to ours.
LANG_MAP = {
    "eng": "en",
    "spa": "es",
    "vie": "vi",
    "cmn": "zh",
    "zho": "zh",
    "ara": "ar",
}
OUR_LANGS = {"en", "es", "vi", "zh", "ar"}

# Cache of successful TTS results. An OrderedDict remembers insertion order,
# so we can throw away the oldest entry when it gets too big.
_tts_cache = OrderedDict()


def _api_key():
    """Return the API key from the environment (empty string if missing)."""
    return os.environ.get("ELEVENLABS_API_KEY", "").strip()


def _voice_id():
    return os.environ.get("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")


def _tts_model():
    return os.environ.get("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5")


def _stt_model():
    return os.environ.get("ELEVENLABS_STT_MODEL", "scribe_v2")


def enabled() -> bool:
    """True when an ElevenLabs API key is set."""
    return bool(_api_key())


def tts(text: str, lang: str) -> bytes | None:
    """Turn text into MP3 audio. Returns None if anything goes wrong."""
    try:
        key = _api_key()
        if not key:
            return None

        # Clean up the text and make sure it is not too long.
        text = (text or "").strip()
        if not text:
            return None
        text = text[:MAX_TTS_CHARS]

        voice_id = _voice_id()
        model = _tts_model()

        # If we already made this audio, reuse it.
        cache_key = (text, lang, voice_id, model)
        if cache_key in _tts_cache:
            _tts_cache.move_to_end(cache_key)
            return _tts_cache[cache_key]

        response = httpx.post(
            TTS_URL_BASE + voice_id,
            params={"output_format": "mp3_44100_128"},
            headers={
                "xi-api-key": key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            json={"text": text, "model_id": model, "language_code": lang},
            timeout=TIMEOUT_SECONDS,
        )

        # Anything other than 200 means ElevenLabs refused the request.
        if response.status_code != 200:
            body = response.text[:200]
            print(f"[voice] tts failed: status {response.status_code}: {body}")
            return None

        audio = response.content

        # Remember this result. Drop the oldest one if the cache is full.
        _tts_cache[cache_key] = audio
        if len(_tts_cache) > CACHE_SIZE:
            _tts_cache.popitem(last=False)
        return audio
    except Exception as error:
        print(f"[voice] tts warning: {error}")
        return None


def _normalize_language(code: str | None) -> str:
    """Turn a language code from ElevenLabs into one of our 2-letter codes."""
    if not code:
        return "en"
    code = code.strip().lower()
    code = LANG_MAP.get(code, code)
    if code in OUR_LANGS:
        return code
    return "en"


def stt(audio: bytes, filename: str, content_type: str, lang: str | None) -> dict | None:
    """Turn recorded audio into text. Returns None if anything goes wrong."""
    try:
        key = _api_key()
        if not key:
            return None
        if not audio:
            return None

        # The form fields ElevenLabs expects. language_code is optional.
        data = {"model_id": _stt_model()}
        if lang:
            data["language_code"] = lang

        response = httpx.post(
            STT_URL,
            headers={"xi-api-key": key},
            data=data,
            files={
                "file": (
                    filename or "audio.webm",
                    audio,
                    content_type or "audio/webm",
                )
            },
            timeout=TIMEOUT_SECONDS,
        )

        if response.status_code != 200:
            body = response.text[:200]
            print(f"[voice] stt failed: status {response.status_code}: {body}")
            return None

        result = response.json()
        text = str(result.get("text", "")).strip()
        if not text:
            return None

        # Prefer the language we asked for. Otherwise use what ElevenLabs found.
        language = _normalize_language(lang or result.get("language_code"))
        return {"text": text, "language": language}
    except Exception as error:
        print(f"[voice] stt warning: {error}")
        return None
