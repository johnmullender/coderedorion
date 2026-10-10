# HelpLine Houston: code contract

Every module is built against this file. If you own a module, implement exactly these names and shapes. If you need something from another module, use only what is written here.

App root: `/mnt/project-files/code/helpline/`. Python 3.11+, everything **synchronous** (no async), standard library where possible. Allowed packages: fastapi, uvicorn, google-genai, twilio, python-dotenv, httpx, python-multipart, pytest, psycopg[binary] (v3). Run tests from the app root with `python3 -m pytest -q`.

Houston time zone everywhere: `zoneinfo.ZoneInfo("America/Chicago")`.

Supported languages: `"en" | "es" | "vi" | "zh" | "ar"`.
Needs (categories): `"food" | "shelter" | "hygiene" | "clinic" | "library" | "cooling" | "legal"`.

---

## Resource record (`data/resources.json`, a JSON list)

```json
{
  "id": "sample-third-ward-pantry",
  "name": "Sample Third Ward Pantry",
  "category": ["food"],
  "address": "123 Example St, Houston, TX 77004",
  "lat": 29.7205, "lng": -95.3600,
  "hours": {"mon": ["09:00-12:00", "13:00-17:00"], "tue": [], "wed": ["13:00-18:00"],
            "thu": [], "fri": [], "sat": [], "sun": []},
  "requirements": {"id_required": true, "proof_of_income": false, "zip_restricted": []},
  "languages": ["en", "es"],
  "phone": "713-555-0100",
  "notes": "Bring a bag.",
  "source_url": "",
  "last_verified": null
}
```
- `hours`: all 7 keys `mon`..`sun`, each a list of `"HH:MM-HH:MM"` ranges. `"00:00-24:00"` means open all day. A range whose end is earlier than its start crosses midnight (`"18:00-07:00"` = 6 PM until 7 AM the next morning).

## `matching.py`

```python
DATA_DIR: pathlib.Path        # <app root>/data
def load_resources(path=None) -> list[dict]          # default DATA_DIR / "resources.json"
def haversine_miles(lat1, lng1, lat2, lng2) -> float
def is_open(resource: dict, when: datetime) -> bool  # `when` is tz-aware (Houston)
def find_matches(need: str, lat: float, lng: float, when: datetime | None = None,
                 resources: list[dict] | None = None, limit: int = 3) -> dict
```
`find_matches` returns:
```python
{"options": [match, ...],   # up to `limit`, open now, in `need`, nearest first
 "next_open": match | None} # only when options is empty: the place in `need` that opens soonest (within 7 days)
```
A `match` is a copy of the resource plus:
`"distance_mi": float (1 decimal)`, `"open_now": bool`, `"closes_at": "HH:MM" | None` (end of the current range when open; `"24:00"`-style all-day → None), `"opens_at": "Mon 09:00" | None` (only for next_open).
`when=None` means now in Houston. `resources=None` means `load_resources()` (cache it).

## `location.py`

```python
STATION_LAT: float = 29.7199   # UH Student Center South (the kiosk's fixed spot)
STATION_LNG: float = -95.3422
def resolve_location(text: str | None) -> dict | None
```
Returns `{"lat": float, "lng": float, "label": str, "source": "zip" | "landmark" | "geocode"}` or `None`.
Order: (1) a 5-digit Houston zip in the text, looked up in `data/zips.json`; (2) landmark table (case-insensitive, word-boundary match; "UH", "University of Houston", "TSU", "MLK", "Emancipation Park", "downtown", "midtown", "Third Ward", "Med Center"…); (3) OpenStreetMap Nominatim via httpx (`User-Agent: HelpLineHouston/0.1 (hackathon)`, viewbox-bounded to Houston, 1 request/sec max, 5s timeout, results cached in memory, any error → None). Text that has nothing location-like (empty/None) → None without calling the network.

`data/zips.json`: `{"77004": {"lat": 29.72, "lng": -95.36, "label": "Third Ward"}, ...}`.

## `brain.py`

```python
def understand(text: str, history: list[dict], lang_hint: str | None = None) -> dict
def answer(question: str, language: str, need: str | None, matches: list[dict],
           channel: str, unmet_next: dict | None = None, history: list[dict] | None = None) -> str
```
`history` is a list of `{"role": "user" | "bot", "text": str}`, oldest first, at most the last 6.

`understand` returns exactly these keys:
```python
{"intent": "find" | "next_option" | "followup" | "help" | "other",
 "need": one of the needs or None,
 "language": one of the languages,
 "location_text": str | None,      # place words copied from the message ("near UH", "77004")
 "option_number": int | None}      # for "2", "option 3", "the second one"
```
- `help` = the person asks for a human / "HELP" / "AYUDA" / "211".
- `followup` = a question about the places already given ("is it open tomorrow?", "is it far?"), not a new search.
- If `lang_hint` is given (the kiosk knob), `language` must equal it.

`answer` writes the reply in `language` using ONLY facts present in `matches` / `unmet_next` (name, address, distance_mi, closes_at, opens_at, requirements, phone, notes, hours). The first match is the one being recommended. For `channel == "sms"`: ≤ 300 characters, includes name, distance, closing time, address, and ends with a hint to reply 2/3 for other options (when more than one match). For `channel == "kiosk"`: 1–2 short spoken sentences, no URLs, no emoji. With no matches and `unmet_next`, say nothing is open now and when/where the next one opens. With no matches and no `unmet_next`, say nothing was found and suggest calling 211.

Gemini: `google-genai` client, key from env `GEMINI_API_KEY`, model from env `GEMINI_MODEL` (default `"gemini-2.5-flash"`), load `.env` with python-dotenv. `understand` must use structured JSON output (response schema). **Both functions must work with no key or on any API error** via a rule-based fallback (keyword lists in all 5 languages for needs/help, digit detection for option_number, simple template replies in en/es and English for the rest). Never raise to the caller.

## `main.py` (FastAPI app object named `app`)

`POST /ask` JSON in:
```json
{"channel": "sms" | "kiosk", "session_id": "str", "text": "str", "lang": "es" | null, "lat": null, "lng": null}
```
JSON out:
```json
{"reply": "text to send/speak",
 "language": "es",
 "need": "food" | null,
 "intent": "find",
 "options": [{"n": 1, "id": "...", "name": "...", "address": "...", "distance_mi": 0.9,
              "open_now": true, "closes_at": "18:00", "opens_at": null, "phone": "..."}],
 "selected": 1,              // which option the reply is about (1-based), 0 if none
 "unmet": false,             // true when a need was searched and nothing was open
 "ask_location": false}      // true when we asked "what zip or street are you near?"
```
Kiosk: if lat/lng are null, use `location.STATION_LAT/LNG`. SMS: lat/lng from `resolve_location(location_text or text)`, else the session's last known location, else ask for location.
Session memory: in-memory dict by session_id, keeps last options, last need, last location, language, last 6 history items; expires after 30 minutes idle.
`next_option` with a number picks from the session's last options without searching again (out of range → say so). `followup` answers about the session's last options. `help` replies with 211 info and the selected place's phone. New need → new search.

`POST /sms`: Twilio form fields `From`, `Body` → same logic as /ask (channel "sms", session_id = From) → TwiML (`twilio.twiml.messaging_response.MessagingResponse`), media type `application/xml`.

`GET /kiosk` → `static/kiosk.html`. `GET /dashboard` → `static/dashboard.html`. `GET /` → small JSON health message.

`GET /api/requests` → 
```json
{"requests": [{"id": 1, "time": "2026-10-10T19:05:00-05:00", "channel": "sms", "need": "food",
               "language": "es", "lat": 29.72, "lng": -95.34, "matched": true,
               "place": "Sample Third Ward Pantry", "text": "necesito comida"}],
 "counts": {"food": 3, "shelter": 1},
 "unmet_counts": {"shelter": 1}}
```
Newest last, at most the last 500. Logged on every /ask that has a need. No phone numbers or session ids in this output.

---

## `db.py` (Tiger Data = hosted Postgres + TimescaleDB)

Env `DATABASE_URL` (the Tiger Cloud service URL, `postgres://...?sslmode=require`). Uses psycopg 3, a new short-lived connection per call is fine (`psycopg.connect(DATABASE_URL, connect_timeout=5, autocommit=True)`).
**If `DATABASE_URL` is unset or the database is unreachable, every function falls back to in-memory behaviour and never raises** (the demo must still work offline).

```python
def enabled() -> bool                       # DATABASE_URL set
def init_db() -> None                       # create tables if missing; called once at app startup
def get_resources() -> list[dict]           # resource records (same dict shape as resources.json);
                                            # from the DB if enabled and non-empty, else matching.load_resources()
def upsert_resources(rows: list[dict]) -> int   # insert/update by id, returns count
def log_request(entry: dict) -> dict        # entry has the /api/requests item fields except id; returns it with "id"
def recent_requests(limit: int = 500) -> list[dict]   # oldest first (newest last), same item shape as /api/requests
```
Tables:
- `resources(id text primary key, name text, category text[], lat double precision, lng double precision, data jsonb not null, updated_at timestamptz default now())` — `data` holds the whole record.
- `requests(id bigserial, time timestamptz not null, channel text, need text, language text, lat double precision, lng double precision, matched boolean, place text, text text)`; after creating it, try `SELECT create_hypertable('requests', by_range('time'), if_not_exists => TRUE)` and ignore the error if TimescaleDB isn't installed (plain Postgres for local tests). Note: a hypertable can't have a primary key that excludes `time`, so no PK on id.
In-memory fallback for requests: a module list capped at 500.

`seed_db.py` (script): `python3 seed_db.py` loads `data/resources.json` into the DB with upsert_resources and prints the count.

## `voice.py` (ElevenLabs speech, used by the kiosk through the server so the key stays on the server)

Env `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` (default `"JBFqnCBsd6RMkjVDRZzb"`), `ELEVENLABS_TTS_MODEL` (default `"eleven_flash_v2_5"`, which covers en/es/vi/zh/ar), `ELEVENLABS_STT_MODEL` (default `"scribe_v2"`). httpx, 15 s timeout, header `xi-api-key`.
```python
def enabled() -> bool
def tts(text: str, lang: str) -> bytes | None
    # POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128
    # json {"text": text, "model_id": TTS_MODEL, "language_code": lang}; returns MP3 bytes; None on any error / no key
def stt(audio: bytes, filename: str, content_type: str, lang: str | None) -> dict | None
    # POST https://api.elevenlabs.io/v1/speech-to-text, multipart: model_id, file, language_code (if lang)
    # returns {"text": str, "language": str}; None on any error / no key / empty text
```
Small in-memory LRU cache (up to 100 entries) for tts by (text, lang).

## Extra `main.py` routes
- `POST /tts` JSON `{"text": str, "lang": "es"}` → `audio/mpeg` bytes, or HTTP 503 `{"error": "tts unavailable"}`.
- `POST /stt` multipart `file` (audio blob, e.g. `audio/webm`) + form `lang` → `{"text": ..., "language": ...}`, or HTTP 503 `{"error": "stt unavailable"}`.
- Request logging and `/api/requests` go through `db.log_request` / `db.recent_requests`; matching uses `resources=db.get_resources()`. `db.init_db()` runs on startup.

## Arduino ↔ kiosk serial protocol (9600 baud, one line per message, `\n` terminated)

Arduino → laptop:
- `READY` once after boot
- `BTN` short press (released before 2 s)
- `HOLD` long press (fires once at 2 s while still held)
- `KNOB:<0-1023>` when the reading changes by more than 15 since the last one sent (and once at boot)

Laptop → Arduino:
- `LCD1:<text>` row 1 (top), padded/truncated to 16 chars
- `LCD2:<text>` row 2, padded/truncated to 16 chars, stops any scrolling
- `SCROLL2:<text>` row 2 scrolls the text right-to-left in a loop (~300 ms per step) if longer than 16 chars, otherwise shows it static
- `BEEP:<n>` n short beeps (1–5)
- `ERR` one long low tone (~600 ms)

Text sent to the LCD is plain ASCII (the kiosk strips accents and non-ASCII before sending). Lines are at most 120 chars.

## Kiosk behaviour (`static/kiosk.html`, Chrome on the hidden laptop)
Knob → language when idle (5 equal bands: EN, ES, VI, ZH, AR, same as `hardware-test.ino`), → option 1/2/3 after an answer. Short press → `BEEP:1`, LCD1 "Listening...", record the mic with MediaRecorder and send it to `POST /stt` (ElevenLabs Scribe); press again or 8 s ends recording; if /stt fails, fall back to Chrome speech recognition in the chosen language (`en-US`, `es-US`, `vi-VN`, `zh-CN`, `ar-SA`); LCD1 "Thinking...", `POST /ask` (channel "kiosk", session id random per visit, `lang` = knob language) → speak `reply` (ElevenLabs via `POST /tts`, falling back to `speechSynthesis` if it returns non-200 or fails), `BEEP:2`, LCD1 `Option 1/3 0.9mi`, `SCROLL2:<name> - <address>`. Long press → speak all options (or, when idle, the list of things you can ask about). 60 s idle → reset to LCD1 "Press for help", LCD2 = language, new session id.
