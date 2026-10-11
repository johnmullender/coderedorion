# HelpLine Houston

Text one number in any language and get the place in Houston that can help you right now. CodeRED Orion 2026.

- `main.py`: the web server (`/ask`, `/sms`, `/tts`, `/stt`, `/kiosk`, `/dashboard`, `/api/requests`)
- `brain.py`: Gemini, which understands the message and writes the reply (works without a key using simple rules)
- `matching.py`: picks the place in plain code (open now, nearest first). The AI never picks the place
- `location.py`: zip codes, landmarks, and OpenStreetMap lookup
- `db.py`: Tiger Data database (places and request log). Without it, the app uses `data/resources.json`
- `voice.py`: ElevenLabs voices and speech-to-text for the help station
- `static/kiosk.html`: the help station page (Chrome on the hidden laptop)
- `static/dashboard.html`: the live map for the city
- `arduino/station/station.ino`: the help station firmware (UNO R4 WiFi)
- `eligibility.py`: yes/no questions from each place's stated rules
- `transit.py`: the next METRO bus from METRO's published schedule
- `scripts/`: geocode, check quotes against sources, load the METRO schedule
- `CONTRACT.md`: how all the pieces fit together. `AGENT_LOG.md`: who wrote and reviewed what

## Run it on your laptop

Needs Python 3.10 or newer (`python3 --version`).

```bash
python3 -m venv .venv
source .venv/bin/activate     # do this in every new terminal
python -m pip install -r requirements.txt
cp .env.example .env          # then paste your keys into .env
python -m uvicorn main:app --reload
```
Open http://localhost:8000/dashboard and http://localhost:8000/kiosk (use Chrome for the kiosk).

Every key is optional. Without a key, that piece falls back:
| Key in `.env` | Without it |
|---|---|
| `GEMINI_API_KEY` | Simple keyword rules and fixed-sentence replies in all 5 languages |
| `DATABASE_URL` (Tiger Cloud service URL) | `data/resources.json` + in-memory request log |
| `ELEVENLABS_API_KEY` | Chrome's built-in voices and speech recognition |

## Tiger Data
1. Create a free service at console.cloud.timescale.com and copy its connection string into `.env` as `DATABASE_URL`.
2. Run `python seed_db.py` to load the places. The request log table is created on its own the first time the server starts.

## Texting (Twilio)
1. `ngrok http 8000` gives you a public `https://...ngrok-free.app` URL.
2. In the Twilio console, set your number's "A message comes in" webhook to `https://<that-url>/sms` (HTTP POST).
3. Text the number from a verified phone.

## Help station
1. Upload `arduino/station/station.ino` with the Arduino IDE. Close the Serial Monitor afterwards, because only one program can use the port.
2. Open `/kiosk` in Chrome, click **Connect station**, and pick the Arduino's port.
3. Turn the knob to choose a language, then press the button and ask.

## Tests

Step-by-step manual checks for every feature are in `TESTING.md`.
```bash
python -m pytest -q
```

**Data:** `data/resources.json` holds 99 real Houston places gathered from official pages. Each is marked `needs_review` until the team checks it (TESTING.md step 12). The tests use the 12 sample places in `tests/fixtures/`.
