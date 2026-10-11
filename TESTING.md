# Testing HelpLine Houston, feature by feature

Work top to bottom. Each step says what to do and what you should see. Steps 1–5 need nothing but your laptop. The later steps each add one piece: Gemini, Tiger Data, ElevenLabs, the Arduino, then Twilio.

All commands run in the repo folder in a terminal. The server address is `http://localhost:8000`.

---

## 0. One-time setup

On a Mac the command is **`python3`**, not `python`. `python` only works once the virtual environment below is turned on.

1. **Check your Python version.** It needs to be 3.10 or newer.
   ```bash
   python3 --version
   ```
   If it says 3.9 or lower, or "command not found", install Python 3.12 from https://www.python.org/downloads/ (or run `brew install python@3.12`). Then open a new terminal.

2. **Get the code and set it up.** This makes a private Python environment (`.venv`) just for this project.
   ```bash
   git clone https://github.com/johnmullender/coderedorion
   cd coderedorion
   python3 -m venv .venv
   source .venv/bin/activate
   python -m pip install -r requirements.txt
   cp .env.example .env
   ```
   **Expect:** your prompt now starts with `(.venv)`. Leave `.env` empty for now. Every feature has a fallback, so the app runs without any keys.

3. **Every time you open a new terminal** for this project, run these two lines first:
   ```bash
   cd coderedorion
   source .venv/bin/activate
   ```
   You're in the right folder when `ls` shows `main.py`, `TESTING.md` and `requirements.txt`.

If you already cloned the repo before, run `git pull` inside `coderedorion` to get the latest code, then do step 2 from `python3 -m venv .venv` onward.

**About the sample data.** `data/resources.json` has 12 made-up places (every name starts with "Sample"). Their hours decide what counts as "open now", which these steps use:

| Need | Sample place | Open |
|---|---|---|
| shelter | Sample 24/7 Shelter | **always**: use it when you need a guaranteed match |
| shelter | Sample Overnight Shelter | every night 7 PM–7 AM |
| food | Sample UH Food Closet | every day 11 AM–7 PM |
| cooling | Sample Cooling Center | every day 9 AM–9 PM |
| legal | Sample Legal Aid | Tue + Thu 1–4 PM only: use it to test "nothing open" |
| hygiene, clinic, library | one or two places each | weekday daytime |

---

## 1. Automated tests (30 seconds)

```bash
python -m pytest -q
```
**Expect:** `137 passed, 8 skipped`. The 8 skipped tests need a local Postgres, so that's fine.

## 2. Start the server

```bash
python -m uvicorn main:app --reload
```
Leave this terminal running. Open a **second terminal** for the next steps (`cd coderedorion` there too; the curl commands don't need the `.venv`).
**Expect:** the log ends with `Uvicorn running on http://127.0.0.1:8000`. Opening http://localhost:8000 in a browser shows `{"ok":true,"service":"HelpLine Houston"}`.

## 3. Text line, without a phone

Each `curl` acts like one text message from the same phone number, so they form one conversation. The reply comes back as Twilio XML; read the text inside `<Message>`.

**3a. A basic search**
```bash
curl -s -X POST localhost:8000/sms -d From=+15550001 --data-urlencode "Body=I need a place to sleep near UH"
```
**Expect:** Sample 24/7 Shelter, its distance in miles, its address, and "Reply 2 or 3 for other options".

**3b. Pick another option**
```bash
curl -s -X POST localhost:8000/sms -d From=+15550001 --data-urlencode "Body=2"
```
**Expect:** a different shelter (option 2). Sending `5` instead should say which numbers are valid.

**3c. Ask a follow-up**
```bash
curl -s -X POST localhost:8000/sms -d From=+15550001 --data-urlencode "Body=is it open tomorrow?"
```
**Expect:** an answer about the place you're on. Without Gemini this is basic; it gets much better in step 6.

**3d. Ask for a human**
```bash
curl -s -X POST localhost:8000/sms -d From=+15550001 --data-urlencode "Body=HELP"
```
**Expect:** the 211 number and the selected place's phone.

**3e. No location given**, using a new phone number
```bash
curl -s -X POST localhost:8000/sms -d From=+15550002 --data-urlencode "Body=I need a shelter"
curl -s -X POST localhost:8000/sms -d From=+15550002 --data-urlencode "Body=77004"
```
**Expect:** the first reply asks what zip code or street you're near. The second one finishes the search with no need to repeat "shelter".

**3f. Spanish**
```bash
curl -s -X POST localhost:8000/sms -d From=+15550003 --data-urlencode "Body=necesito un refugio cerca de UH"
curl -s -X POST localhost:8000/sms -d From=+15550003 --data-urlencode "Body=2"
```
**Expect:** both replies in Spanish, including after "2".

**3g. Nothing open** (any time except Tue/Thu 1–4 PM)
```bash
curl -s -X POST localhost:8000/sms -d From=+15550004 --data-urlencode "Body=I need a lawyer near 77004"
```
**Expect:** "nothing open now", plus when and where the next legal place opens. This shows up as a red **unmet** pin in step 4.

**3h. Other languages.** Without Gemini, Vietnamese, Chinese and Arabic messages are understood, but the replies come back in English. Step 6 fixes that.
```bash
curl -s -X POST localhost:8000/sms -d From=+15550005 --data-urlencode "Body=tôi cần chỗ ngủ tối nay gần UH"
```
**Expect:** a shelter match.

## 4. Dashboard

Open http://localhost:8000/dashboard.
**Expect:**
- A map around UH with one pin per search from step 3, colored by need.
- The legal search from 3g as a pin with a **red ring**.
- The side panel shows totals, a bar for each need with unmet counts in red, and the latest requests.

Send another curl from step 3 while the page is open. A new pin should **pulse in within 2 seconds**. Click a pin to see what was asked; no phone numbers appear anywhere.

## 5. Help station page, without the Arduino

Open http://localhost:8000/kiosk in **Chrome**. You don't need to click "Connect station". The debug panel stands in for the hardware.

> **Watch the screen, not the board.** In this step, "the LCD box" means the green **LCD preview** box on the web page, under "Station". The real LCD on the Arduino doesn't change yet. If the board is plugged in, it keeps showing whatever sketch is on it (for example "LONG press / 173 English" from `hardware-test.ino`). The real LCD only joins in at step 10, after you upload `station.ino` and click Connect station.

1. **Language:** drag the **knob slider**. The language changes through English → Espanol → Tieng Viet → Zhongwen → Arabic. The "LCD" box shows the language on row 2.
2. **Typed question:** type `I need a shelter` in the box and press Enter. The reply appears and is **spoken**: Chrome's voice for now, ElevenLabs after step 8. The LCD box shows something like `Option 1/2 0.7mi` (the count depends on how many places are open) and the place name and address.
3. **Browse options:** drag the knob slider. Each new band picks option 1, 2 or 3, and each one is spoken and shown.
4. **Long press:** click **Long press**. It reads out all the options.
5. **Spoken question:** click **Short press**, allow the microphone, say "I need food", then click **Short press** again or wait 8 seconds. Without ElevenLabs, Chrome's own speech recognition is used.
6. **Idle reset:** leave the page alone for 60 seconds. It returns to "Press for help" and starts a new session.
7. **Serial log:** the log at the bottom lists every line that *would* go to the Arduino (`LCD1:`, `SCROLL2:`, `BEEP:2` …).

Kiosk requests also show up on the dashboard, at the UH Student Center.

## 6. Turn on Gemini

1. Get a free key at https://aistudio.google.com, then put `GEMINI_API_KEY=...` in `.env`. Check which Flash model AI Studio lists, and change `GEMINI_MODEL` if it's newer than `gemini-2.5-flash`.
2. Stop the server (Ctrl+C) and start it again.
3. Re-run 3c, 3f and 3h.

**Expect:**
- Follow-ups get real answers.
- Vietnamese, Chinese and Arabic get replies **in that language**.
- Messy texts work, e.g. `im hungry n broke, near tsu`.

**Check the "no made-up places" claim:** every place named in a reply should be a "Sample …" place from `resources.json`.

## 7. Turn on Tiger Data

1. Create a free service at https://console.cloud.timescale.com and copy its connection string (it ends in `sslmode=require`) into `.env` as `DATABASE_URL=...`.
2. Load the places:
   ```bash
   python seed_db.py
   ```
   **Expect:** `Loaded 12 resources into Tiger Data`.
3. Restart the server and send a couple of texts from step 3.
4. In the Tiger console's SQL editor, run:
   ```sql
   SELECT time, channel, need, language, matched, place FROM requests ORDER BY time DESC LIMIT 10;
   SELECT * FROM timescaledb_information.hypertables;
   ```
   **Expect:** your requests in the first query, and `requests` listed as a hypertable in the second. That's the line for the judges.
5. **Prove it's the database talking:** in the console, run `UPDATE resources SET data = jsonb_set(data, '{name}', '"Tiger Test Shelter"') WHERE id = 'sample-24-7-shelter';`. Wait a minute, because places are cached for 60 seconds, then redo 3a. The reply should say "Tiger Test Shelter". Run `python seed_db.py` again to undo it.
6. **Fallback:** put a wrong password in `DATABASE_URL` and restart. Texts still work, with a one-time "Database unavailable, using in-memory storage" warning.

## 8. Turn on ElevenLabs

1. Put `ELEVENLABS_API_KEY=...` in `.env` (from elevenlabs.io → profile → API keys) and restart.
2. **Voice:**
   ```bash
   curl -s -X POST localhost:8000/tts -H "content-type: application/json" -d '{"text":"Hola, el refugio está abierto ahora.","lang":"es"}' -o test.mp3
   open test.mp3        # Windows: start test.mp3
   ```
   **Expect:** a natural Spanish voice. If `test.mp3` is tiny or won't play, open it in a text editor to see the error. A `503` means the key isn't being read.
3. **Listening, round trip:**
   ```bash
   curl -s -X POST localhost:8000/stt -F file=@test.mp3 -F lang=es
   ```
   **Expect:** `{"text":"Hola, el refugio está abierto ahora.","language":"es"}` or very close.
4. **In the kiosk:** repeat step 5. The spoken answers now use the ElevenLabs voice, in all 5 languages. Spoken questions go to ElevenLabs Scribe, so try Spanish or Vietnamese after turning the knob.
5. **Fallback:** delete the key and restart. The kiosk goes back to Chrome's voice and recognition, and you only have to speak once.

## 9. Arduino station on its own

1. Open `arduino/station/station.ino` in the Arduino IDE, choose **Arduino UNO R4 WiFi** and upload.
2. **Expect:** the LCD shows "Press for help" / "English", and the buzzer stays quiet.
3. Open **Serial Monitor** at **9600 baud** with line ending set to **Newline**.
   - Turn the knob: `KNOB:<number>` lines appear.
   - Tap the button: `BTN`. Hold it for 2 seconds: `HOLD` (and no `BTN` when you let go).
4. Type these into Serial Monitor one at a time:

   | Send | Expect |
   |---|---|
   | `LCD1:Hello Houston` | top row changes |
   | `LCD2:Row two` | bottom row changes |
   | `SCROLL2:Sample 24/7 Shelter - 202 Example Rd, Houston, TX 77004` | bottom row scrolls in a loop |
   | `BEEP:2` | two short beeps |
   | `ERR` | one long beep |

5. **Close Serial Monitor** before step 10. Only one program can use the port at a time.

## 10. Full help station (Arduino + laptop)

1. Plug the Arduino into the laptop. Open http://localhost:8000/kiosk in Chrome, click **Connect station** and pick the Arduino's port.
2. Turn the real knob. The LCD shows each language.
3. Press the button: 1 beep, "Listening...". Ask "where can I sleep tonight?", then press again.
4. **Expect:** "Thinking...", then 2 beeps. The answer is spoken, the LCD top row shows something like `Option 1/2 0.7mi`, and the bottom row scrolls the name and address.
5. Turn the knob to browse the options. Hold the button for 2 seconds to hear all of them.
6. Press again and ask a follow-up ("is it far?"). It should answer about the same places.
7. Walk away for 60 seconds. The LCD goes back to "Press for help".
8. **Demo run:** knob to Tieng Viet, ask "tôi cần chỗ ngủ tối nay". The answer should be spoken in Vietnamese.

## 11. Real texting (Twilio)

1. In a third terminal, run `ngrok http 8000` and copy the `https://….ngrok-free.app` URL.
2. Twilio console → Phone Numbers → your number → "A message comes in": `https://<ngrok-url>/sms`, **HTTP POST**. Save.
3. Make sure the phone you're texting from is under **Verified Caller IDs** (trial accounts only reply to verified numbers).
4. Text the Twilio number: `necesito comida cerca de UH`.
5. **Expect:** a Spanish reply within a few seconds, starting with "Sent from your Twilio trial account". A pin also drops on the dashboard.
6. Repeat the conversations from step 3 from a real phone (`2`, a follow-up, `AYUDA`).

---

## If something doesn't work

| Problem | Likely cause |
|---|---|
| `python: command not found` | Turn on the environment with `source .venv/bin/activate`, or use `python3` |
| `ModuleNotFoundError` | The `.venv` isn't on (no `(.venv)` in your prompt): run `source .venv/bin/activate`. Then `python -m pip install -r requirements.txt` |
| `error: externally-managed-environment` from pip | You're installing outside the `.venv`. Turn it on first |
| `TypeError: unsupported operand type(s) for \|` | Your Python is older than 3.10. See step 0.1 |
| Port 8000 already in use | Another server is still running. Close it, or use `--port 8001` |
| Kiosk: no "Connect station" port listed | Not using Chrome, or Serial Monitor still has the port open |
| Kiosk: mic never asks for permission | Open the page as `localhost`, not your laptop's IP address |
| LCD blank after upload | Check the 4 LCD wires (GND, 5V, SDA→A4, SCL→A5) and that the shield switch is on 5V. Run `hardware-test.ino` to compare |
| Text gets no reply | The phone isn't verified in Twilio, the ngrok URL changed (it changes on each restart), or the webhook isn't `/sms` with POST |
| Replies in English when you wrote in Vietnamese, Chinese or Arabic | `GEMINI_API_KEY` is missing or wrong. Check the server log |
| Everything says "nothing open" | Check the time against the sample hours table at the top |
| Any key change does nothing | Restart the server after editing `.env` |
