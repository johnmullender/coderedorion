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

`answer` writes the reply in `language` using ONLY facts present in `matches` / `unmet_next` (name, address, distance_mi, closes_at, opens_at, requirements, phone, offers as labels only, hours). `notes` is never sent to Gemini (internal research remarks). The first match is the one being recommended. For `channel == "sms"`: ≤ 300 characters, includes name, distance, closing time, address, and ends with a hint to reply 2/3 for other options (when more than one match). For `channel == "kiosk"`: 1–2 short spoken sentences, no URLs, no emoji. With no matches and `unmet_next`, say nothing is open now and when/where the next one opens. With no matches and no `unmet_next`, say nothing was found and suggest calling 211.

Gemini: `google-genai` client, key from env `GEMINI_API_KEY`, model from env `GEMINI_MODEL` (default `"gemini-2.5-flash"`), load `.env` with python-dotenv. `understand` must use structured JSON output (response schema). **Both functions must work with no key or on any API error** via a rule-based fallback (keyword lists in all 5 languages for needs/help, digit detection for option_number, simple template replies in all 5 languages). Never raise to the caller.

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
Knob → language when idle (5 equal bands: EN, ES, VI, ZH, AR, same as `hardware-test.ino`), → option 1/2/3 after an answer. Short press → `BEEP:1`, LCD1 "Listening...", record the mic with MediaRecorder and send it to `POST /stt` (ElevenLabs Scribe, no language sent so it detects the spoken language); press again or 8 s ends recording; if /stt fails, fall back to Chrome speech recognition in the chosen language (`en-US`, `es-US`, `vi-VN`, `zh-CN`, `ar-SA`); LCD1 "Thinking...", `POST /ask` (channel "kiosk", session id random per visit, `lang` = language Scribe heard, else knob language; typed debug questions send `null` so the server detects it; the kiosk then switches to the reply's language) → speak `reply` (ElevenLabs via `POST /tts`, falling back to `speechSynthesis` if it returns non-200 or fails), `BEEP:2`, LCD1 `Option 1/3 0.9mi`, `SCROLL2:<name> - <address>`. Long press → speak all options (or, when idle, the list of things you can ask about). 60 s idle (or the debug "New person" button) → reset to LCD1 "Press for help", LCD2 = language, new session id.

---

# Phase 2: real data, eligibility questions, bus directions

## Resource record, new fields (all places in `data/resources.json`)

```json
{
  "id": "hpl-central-library",
  "name": "Houston Public Library - Central Library",
  "category": ["library"],
  "address": "500 McKinney St, Houston, TX 77002",
  "lat": 29.7604, "lng": -95.3698,
  "coords_source": "census" | "nominatim" | "manual" | null,
  "hours": {"mon": ["10:00-18:00"], "...": []},
  "hours_quote": "Mon-Thu 10 AM - 6 PM ...",       // exact text copied from source_url, or null
  "eligibility": [ /* rules, see below; [] when the source states none */ ],
  "services": "Free wifi, computers, printing",   // one short line, from the source
  "offers": [{"key": "wifi", "quote": "Free wifi", "url": "https://..."}],  // optional, see "Offers"
  "languages": ["en", "es"],
  "phone": "832-393-1313",
  "notes": "",
  "source_url": "https://...",                     // required, the page the facts came from
  "last_verified": "2026-10-11" | null,            // set by a human spot check
  "needs_review": true                             // true until a human checked it
}
```
- The old `requirements` object stays readable (old sample data) but new data uses `eligibility`.
- `hours` may be all-empty when the source gives no hours; such a place is never "open now" and is only shown as a fallback with "call to confirm".

## Offers (`offers` list, optional)

What a place offers, each item copied from a page:
```json
{"key": "showers", "quote": "Showers are available Tuesdays 8 AM to noon.", "url": "https://..."}
```
- `key` is one of: food_pantry, hot_meals, shelter_beds, showers, laundry, restrooms, clothing, wifi, computers,
  phone_charging, mail, id_help, medical_care, dental_care, pharmacy, mental_health, legal_aid, immigration_help,
  case_management, job_help, cooling, heating, hygiene_items, baby_supplies (`brain.OFFER_KEYS`).
- `quote` is the exact text from `url`; `url` is an http(s) link. A place with no offers has `[]` or no field.
- Replies: an "Offers: ..." line follows the main line (up to 4 labels, in data order, translated via
  `brain.OFFER_LABELS`). On SMS it is dropped after the notes, and only after them.
- Gemini gets the labels only, and its prompt says to describe a place only with its listed offers.
- `scripts/validate.py` checks the key, quote and url; `--check-sources` looks for each offer quote on its own `url`.

## Eligibility rule format (`eligibility` list items)

Every rule MUST carry `source_quote`: the exact sentence copied from `source_url` that states it. A place with no stated rules has `"eligibility": []`. Rules are never invented.

```json
{"type": "id_required",   "source_quote": "Please bring a photo ID."}
{"type": "age",           "min": 18, "max": 24, "source_quote": "..."}      // min or max may be null
{"type": "serves",        "group": "women" | "men" | "families" | "youth" | "veterans" | "seniors", "source_quote": "..."}
{"type": "zip_in",        "zips": ["77004", "77021"], "source_quote": "..."}
{"type": "residency",     "area": "Harris County", "source_quote": "..."}
{"type": "income_limit",  "question": "Is your household income under $2,600 a month?", "source_quote": "..."}
{"type": "note",          "text": "Appointment needed", "source_quote": "..."}   // never asked, only told
```

## `eligibility.py` (pure functions, no network, no Gemini)

```python
RULE_TYPES = ["id_required", "age", "serves", "zip_in", "residency", "income_limit", "note"]
MAX_QUESTIONS = 3

def question_key(rule: dict) -> str | None
    # stable key for the question a rule asks: "id", "age:18-24", "serves:women", "zip", "residency:Harris County",
    # "income:<question text>"; None for "note" (never asked) and for zip_in (checked silently from the location)

def question_text(key: str, rule: dict, language: str) -> str
    # the yes/no question in `language` (all 5), e.g. "Do you have a photo ID with you?"
    # income_limit uses rule["question"] (English) for en; other languages: a fixed translation template where possible,
    # otherwise the English text (brain may translate later)

def check(rule: dict, answers: dict, zip_code: str | None) -> bool | None
    # True = passes, False = ruled out, None = unknown yet (needs a question)
    # answers: {question_key: True | False | None(skip/not sure)}; skip counts as pass (True)
    # zip_in: True if zip_code in rule["zips"], False if zip_code known and not in it, None→True (don't block)

def filter_places(places: list[dict], answers: dict, zip_code: str | None) -> list[dict]
    # drop places with any rule where check(...) is False; keep order

def next_question(places: list[dict], answers: dict, zip_code: str | None, asked: int) -> tuple[str, dict] | None
    # among the first 3 places of `places`, collect unanswered askable rules; return (key, rule) of the one that
    # appears in the most of those places (tie: order of first appearance), or None if none or asked >= MAX_QUESTIONS

def parse_yes_no(text: str) -> bool | None | str
    # True for yes words, False for no words, None for "not sure"/"skip"/"don't know", "unclear" otherwise.
    # All 5 languages: yes/yeah/yep/y/si/sí/claro/có/vâng/dạ/是/对/有/نعم/أجل ; no/nope/n/no tengo/không/没有/不/لا ;
    # skip: skip/not sure/don't know/no sé/omitir/không biết/不知道/跳过/لا أعرف. Case- and accent-insensitive, whole words.

def notes_for(place: dict) -> list[str]
    # texts of "note" rules, plus for each rule answered None (skipped): a reminder ("Bring a photo ID if you have one")
```

## `transit.py` (METRO bus directions from GTFS)

Data lives in Tiger Data tables loaded by `scripts/load_gtfs.py` (`gtfs_stops`, `gtfs_routes`, `gtfs_trips`,
`gtfs_stop_times`, `gtfs_calendar`, `gtfs_calendar_dates`), columns as in the GTFS spec, times kept as seconds after
midnight (ints; may exceed 86400). Without `DATABASE_URL`, transit uses `data/transit_local.json` if present
(written by `load_gtfs.py --local LAT LNG`, the stops within 0.5 mi of that point and every trip serving them),
else returns None.

```python
WALK_MPH = 3.0
MAX_WALK_MI = 0.5
MAX_WAIT_MIN = 90

def directions(from_lat, from_lng, to_lat, to_lng, when: datetime | None = None, source=None) -> dict | None
    # source: an object with the GTFS query methods below (default: DB-backed, or local-file-backed); tests pass a fake
    # Returns the soonest-arriving option, direct first; one transfer only if no direct ride within MAX_WAIT_MIN.
    # If neither leaves within MAX_WAIT_MIN (late at night), the first ride in the next LATER_WAIT_MIN (24 h)
    # is returned with "later": True. A transfer's second bus always leaves within MAX_WAIT_MIN of the connection.
    {"legs": [{"route": "54", "headsign": "Downtown", "board_stop": "Scott @ Holman",
               "alight_stop": "Main @ Gray", "departs": "16:12", "arrives": "16:31", "stops": 6}],
     "walk_to_min": 3, "walk_from_min": 2, "arrive_by": "16:33"}
    # None when no ride is found (caller then names the nearest stops and, on SMS, sends a Google Maps transit link)

def closest_stops(from_lat, from_lng, to_lat, to_lng, language, channel, source=None) -> tuple[str, str] | None
    # (stop nearest the start, stop nearest the destination), named for the channel ("Scott@Holman" on SMS,
    # "Scott at Holman" in speech); None when a point has no stop within NEAREST_STOP_MILES or there is no schedule

def summary(d: dict, language: str, channel: str) -> str
    # kiosk: one spoken sentence per leg; sms: compact "Bus 54 from Scott@Holman 4:12PM, 6 stops to Main@Gray, walk 2 min."
    # times in 12-hour style; all 5 languages via fixed templates (no Gemini)
    # "later": True puts "No buses are running now. The first one:" (translated) in front

class GtfsSource:   # interface used by directions(); DbSource and LocalSource implement it
    def stops_near(self, lat, lng, miles) -> list[dict]          # {"stop_id","name","lat","lng"}
    def service_ids(self, day: date) -> set[str]
    def departures(self, stop_ids: list[str], after_sec: int, before_sec: int, service_ids: set[str]) -> list[dict]
        # {"trip_id","route","headsign","stop_id","stop_sequence","dep_sec"}
    def trip_stops_after(self, trip_id: str, stop_sequence: int) -> list[dict]
        # {"stop_id","name","stop_sequence","arr_sec","lat","lng"} in order
```

## `main.py` changes (phase 2)

- Matching keeps the top 8 open places; eligibility filters them (`eligibility.filter_places`), then the reply uses the top 3.
- Session gains `answers` (dict), `asked` (int), `awaiting_answer` (question key or None), `pending` (the candidate list).
- After a `find` with candidates: `q = eligibility.next_question(...)`; if q, reply with `question_text` and set
  `awaiting_answer` (response field `"asking": "<key>"`). The next message is first tried with `parse_yes_no`;
  "unclear" → fall through to normal understanding (a new need ends the questions).
- When no more questions: answer as before, plus `notes_for` the chosen place, plus the bus line from
  `transit.directions(origin, chosen place)` when found (`"transit": {...}` in the /ask JSON, `null` otherwise).
- New intent `"directions"` from `brain.understand` ("how do I get there", "¿cómo llego?", "đi bằng xe buýt nào", "怎么去", "كيف أصل"):
  replies with `transit.summary` for the selected option, or "no direct bus" + distance.
- Kiosk: short press while a question is pending = yes, long press = no (the kiosk sends text "yes"/"no").
- Eligibility answers are never logged to the DB.

## Data scripts (`scripts/`, run on a machine with internet)

- `scripts/geocode.py`: fills/repairs `lat`/`lng` for every place via the US Census geocoder (then Nominatim), sets `coords_source`.
- `scripts/validate.py`: schema check, Houston-area bounds (lat 29.4–30.2, lng −95.9 to −94.9), unique ids, valid hours,
  every eligibility rule has a non-empty `source_quote`; with `--check-sources`, downloads each `source_url` and
  reports every `source_quote` / `hours_quote` not found in the page text (whitespace- and case-insensitive). Exit code 1 on errors.
- `scripts/load_gtfs.py`: downloads METRO GTFS (`https://metro.resourcespace.com/pages/download.php?ref=4835&ext=zip`, or a local zip path)
  into Tiger Data; `--local LAT LNG` writes `data/transit_local.json` instead.
