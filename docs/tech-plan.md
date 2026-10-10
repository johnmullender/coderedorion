# HelpLine Houston: tech plan, tasks and milestones

Read with `one-number-design.md` (what we're building) and `STATUS.md` (where we're at).
Written Sat Oct 10, 6:25 PM. Hacking ends Sun 1:00 PM.

---

## 1. Final product (agreed)

**Text line.** Text one number in English, Spanish, Vietnamese, Chinese or Arabic. Get back the best open match near you, plus 1–2 other options. Reply "2" or "3" for those, or keep asking follow-ups.

**Help station (kiosk).** A box with the 1602 LCD, the button, the knob, the buzzer, a mic and a speaker. **No screen showing.** The laptop sits hidden inside or behind the box as the brain.
- Turn the knob to pick a language. The LCD shows it.
- Short press: 1 beep, then speak your question.
- It **speaks** the answer and the LCD shows the place and address (scrolling).
- Turn the knob to step through options 1/2/3. Each one is spoken and shown.
- Long press: it reads all the options aloud.
- Follow-ups work the same as texting ("is it open tomorrow?", "what about a shelter?").

**Dashboard.** A separate web page for the city: live map of every request (text and kiosk), what was asked for, and where needs went unmet.

**Coverage.** UH and Third Ward first. Midtown if there's time.

---

## 2. Tech stack decisions

Picked for "vibe coding": one language, one server, as few moving parts as possible.

### 2.1 Backend: **Python + FastAPI**, one app does everything
| Route | What it does |
|---|---|
| `POST /ask` | The brain. Takes `{channel, session_id, text, lang?, lat?, lng?}` and returns the answer + options. Used by SMS and the kiosk |
| `POST /sms` | Twilio webhook. Calls the same logic as `/ask`, replies with TwiML |
| `GET /kiosk` | The kiosk web page (runs in Chrome on the hidden laptop) |
| `GET /dashboard` | The city dashboard page |
| `GET /api/requests` | Request log as JSON, for the dashboard |

Why: FastAPI is the easiest Python web server for AI tools to generate correctly, and one app means one deploy.

### 2.2 AI: **Gemini (Google AI Studio, free key)**
- Use the current **Flash** model listed in AI Studio (fast and free-tier friendly).
- **Call 1, understand:** message + recent conversation in, strict JSON out (use Gemini's structured output / JSON schema):
  `{intent: "find"|"next_option"|"followup"|"other", need: "food"|"shelter"|"hygiene"|"clinic"|"library"|"cooling"|"legal", language: "en"|"es"|"vi"|"zh"|"ar", location_text, option_number?}`
- **Call 2, answer:** the chosen DB rows in, a short reply out in the user's language (≤300 characters for SMS, 1–2 sentences for speech). The prompt says to use **only** facts from the rows.
- The AI never picks the place. Plain Python does (see 2.4).
- Keep the key in a `.env` file (`GEMINI_API_KEY=...`), never in GitHub.

### 2.3 Data: **Google Sheet → `resources.json` in the repo**
- Teammates fill a shared Google Sheet (easy to edit together), one row per place: `id, name, category, address, lat, lng, hours_mon … hours_sun, id_required, notes, languages, phone, source_url`.
- Export to CSV, and a small script converts it to `resources.json`. Gemini can help fill lat/lng and format hours, but **check every row by hand** against the source site.
- 30–50 rows for UH + Third Ward is enough for a great demo. Midtown later.
- Sources: Houston Food Bank pantry finder, Coalition for the Homeless, Houston Public Library branches, City of Houston cooling centers, free clinics.

### 2.4 Matching: **plain Python, no AI**
1. Filter by category (`need`).
2. Filter to **open now** (Houston time, `America/Chicago`). If nothing's open, say so and give the next one to open.
3. Sort by distance (haversine formula on lat/lng).
4. Return the top 3.

### 2.5 Location
- **Kiosk:** fixed lat/lng of where the station "is" (use the UH Student Center).
- **SMS:** in order:
  1. a 5-digit zip in the text → zip centroid table (77004, 77204, 77021, 77003, 77002, 77006…)
  2. a small landmark table ("UH", "TSU", "MLK", "Emancipation Park", "downtown"…)
  3. free geocoding via OpenStreetMap Nominatim (send a User-Agent header, max 1 request per second)
  4. still nothing → ask "What zip code or street are you near?"

### 2.6 Conversation memory
- An in-memory dict keyed by `session_id`: the phone number for SMS, a random id per kiosk visit. Store the last 3 options + the last few messages. Expire after 30 minutes.
- Good enough for a hackathon. If the server restarts, conversations just start over.

### 2.7 SMS: **Twilio trial**
- Free trial number. **Verify every phone that will text it** (Twilio console → Verified Caller IDs).
- Replies start with "Sent from your Twilio trial account". That's fine; mention it if a judge asks.
- Webhook: set the number's "A message comes in" URL to `https://<your-server>/sms`.

### 2.8 Kiosk speech: options and pick
The Arduino can't do speech. The mic and speaker plug into the **laptop**, and a Chrome tab on the laptop does the listening and talking.

| Option | How | Pros | Cons |
|---|---|---|---|
| **A. Browser speech (pick this)** | Chrome's Web Speech API: `SpeechRecognition` (speech → text) + `speechSynthesis` (text → voice), language set from the knob | Free, ~20 lines of code, all 5 languages | Chrome only, needs internet, voice quality varies by language |
| B. Gemini listens | Record audio in the browser, send it to Gemini, which transcribes and understands in one call | One AI call, good with accents | More code, slower |
| C. Cloud speech APIs | Google Cloud Speech / TTS | Best quality | Needs billing setup, more config |

**Pick A. Fall back to B** if recognition is bad for a language.
- Before relying on a language, open Chrome on the demo laptop and check a voice exists for it: run `speechSynthesis.getVoices()` in the console, or check macOS Settings → Accessibility → Spoken Content → System voice → Manage Voices, and download Vietnamese/Arabic/Chinese voices if missing.

**External mic and speaker:** yes, plug them into the laptop, not the Arduino. Any USB mic or wired headset mic works; choose it in the OS sound settings, or in Chrome's site settings for the kiosk page. A Bluetooth or USB speaker hidden in the box sounds better than laptop speakers. Without them, the laptop's own mic and speakers still work.

### 2.9 Kiosk ↔ Arduino link: **Web Serial in Chrome**
- The kiosk page connects straight to the Arduino over USB using Chrome's Web Serial API: one "Connect" click when the page loads, then it's automatic. No Python bridge needed.
- Protocol, one line per message, 9600 baud:
  - Arduino → laptop: `BTN` (short press), `HOLD` (long press), `KNOB:<0-1023>`
  - Laptop → Arduino: `LCD1:<text>`, `LCD2:<text>`, `SCROLL2:<long text>`, `BEEP:<n>`, `ERR`
- **Arduino firmware** = `hardware-test.ino` + reading those commands. The Arduino handles scrolling long addresses on row 2 itself. **Keep the hand-rolled LCD code; no `Wire`/LiquidCrystal_I2C.**

### 2.10 Dashboard
- One HTML page with **Leaflet** (free map, no API key) using OpenStreetMap tiles.
- Polls `/api/requests` every 2 seconds and drops a pin per request, colored by need. Unmet needs (nothing open) get a red pin.
- Side panel: counts by need, and a list of the latest requests (no phone numbers shown).

### 2.11 Hosting
Nobody's deployed before, so here's the plan:
1. **While building: run it on one laptop + ngrok.** `uvicorn main:app --reload`, then `ngrok http 8000` gives a public `https://….ngrok-free.app` URL to paste into Twilio. Free, and it takes about 5 minutes.
2. **For judging: deploy to Render** (free web service). Connect the GitHub repo, set the start command to `uvicorn main:app --host 0.0.0.0 --port $PORT`, and add `GEMINI_API_KEY` under Environment. Point Twilio at the Render URL.
   - The free tier sleeps when idle and the first request takes about 50 seconds. **Open the URL a few minutes before judging** to wake it.
   - If Render gives trouble, the ngrok laptop setup is a fine fallback for the demo.

### 2.12 Repo layout
```
helpline/
  main.py              # FastAPI app: /ask /sms /kiosk /dashboard /api/requests
  brain.py             # Gemini calls (understand + answer)
  matching.py          # filter / open-now / distance / top 3
  location.py          # zip + landmark tables, Nominatim fallback
  data/resources.json
  data/zips.json
  static/kiosk.html    # speech + Web Serial + talks to /ask
  static/dashboard.html
  arduino/station/station.ino
  requirements.txt     # fastapi uvicorn google-genai twilio python-dotenv httpx
  .env                 # GEMINI_API_KEY (not committed)
  .gitignore           # .env
  docs/                # one-number-design.md, STATUS.md, tech-plan.md
```

---

## 3. Tasks in order

Parallel tracks. Each person can grab a track; the numbering is the order within it.

**Track A: setup (first 30 min, one person)**
1. Create the GitHub repo with the layout above; add the docs.
2. Get a Gemini API key (aistudio.google.com).
3. Twilio trial: get a number, verify all teammates' phones.
4. Install ngrok, get the "hello world" FastAPI running locally behind it.

**Track B: data (starts now, runs all evening)**
1. Make the Google Sheet with the columns in 2.3.
2. Fill 30–50 UH + Third Ward places, food and shelter first.
3. Export → `resources.json`; spot-check 10 rows.
4. Make `zips.json` and the landmark table.
5. (Later) Midtown rows.

**Track C: brain + SMS**
1. `matching.py` with fake data: category filter, open now, distance, top 3. Test with a few fake requests.
2. `brain.py` call 1 (understand → JSON); test 15 messy messages in all 5 languages.
3. `brain.py` call 2 (answer in language, facts only from rows).
4. `POST /ask` wiring everything, plus session memory ("2", follow-ups).
5. `POST /sms` → Twilio → real phone round trip.
6. Swap in the real `resources.json`.
7. Log every request (time, channel, need, lang, lat/lng, matched or unmet) for the dashboard.

**Track D: help station**
1. `station.ino`: serial protocol + LCD scroll on row 2 (built from `hardware-test.ino`).
2. `kiosk.html` step 1: Web Serial connect; button → beep → LCD "Listening...".
3. Step 2: speech → text in the knob's language → `POST /ask` → speak the answer + show it on the LCD.
4. Step 3: knob steps through options; long press reads all of them.
5. Step 4: follow-up questions within a visit; 60s idle resets to "Press for help".
6. Build the box (cardboard/foam board), with the LCD, button and knob on the front and the laptop + speaker hidden.

**Track E: dashboard**
1. `dashboard.html` with a Leaflet map centered on UH.
2. Poll `/api/requests`, pins by need, red for unmet.
3. Counts panel + latest requests list.

**Track F: ship + pitch**
1. Deploy to Render; point Twilio at it; test from every phone.
2. Test 20 messy messages and 10 kiosk questions; fix the worst.
3. Devpost page, slides, 2-minute backup video.
4. Rehearse the demo 3×.

---

## 4. Key milestones

| # | Milestone | Target | Done when |
|---|---|---|---|
| M1 | **Skeleton** | Sat 8:00 PM | A text to the Twilio number gets an automatic reply from your code (even just "hi") |
| M2 | **Real answer** | Sat 11:00 PM | A text like "food near UH" gets a real, open place from real data, in the language you wrote in |
| M3 | **Station talks** | Sun 2:00 AM | Press button, speak, hear an answer and see it on the LCD; knob changes options |
| M4 | **Everything connected** | Sun 5:00 AM | Dashboard shows pins from both texts and the station; follow-ups work on both |
| M5 | **Deployed + feature freeze** | Sun 10:00 AM | Running on Render, all phones tested, no new features after this |
| M6 | **Submitted** | Sun 12:30 PM | Devpost submitted (Best Overall, Best Pitch, Best Hardware), demo rehearsed |

**Cut rules if behind:**
- Behind at M2 → drop Midtown and follow-ups; keep "2"/"3" options.
- Behind at M3 → kiosk answers in English and Spanish only.
- Behind at M4 → dashboard becomes a simple list instead of a map.
