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
   - The knob only picks the language on the "Press for help" screen. After an answer, it browses the options instead. Click **New person** to go back to the start screen and pick a language again.
   - The station answers in the language you **ask** in: type or say `necesito un refugio` and the reply is in Spanish, whatever the knob says. The knob language is used when the station can't tell (Chrome's own speech recognition).
2. **Typed question:** type `I need a shelter` in the box and press Enter. The reply appears and is **spoken**: Chrome's voice for now, ElevenLabs after step 8. The LCD box shows something like `Option 1/2 0.7mi` (the count depends on how many places are open) and the place name and address.
3. **Browse options:** drag the knob slider. Each new band picks option 1, 2 or 3, and each one is spoken and shown.
4. **Long press:** click **Long press**. It reads out all the options.
5. **Spoken question:** click **Short press**, allow the microphone, say "I need food", then click **Short press** again or wait 8 seconds. Without ElevenLabs, Chrome's own speech recognition is used.
6. **Idle reset:** leave the page alone for 60 seconds (or click **New person**). It returns to "Press for help" and starts a new session.
7. **Serial log:** the log at the bottom lists every line that *would* go to the Arduino (`LCD1:`, `SCROLL2:`, `BEEP:2` …).

Kiosk requests also show up on the dashboard, at the UH Student Center.

## 6. Turn on Gemini

1. Get a free key at https://aistudio.google.com, then put `GEMINI_API_KEY=...` in `.env`. Check which Flash model AI Studio lists, and change `GEMINI_MODEL` if it's newer than `gemini-2.5-flash`.
2. Stop the server (Ctrl+C) and start it again.
3. Re-run 3c, 3f and 3h.

**Expect:**
- Follow-ups get real answers.
- Replies sound natural and answer follow-up questions. (Without Gemini, all 5 languages already get short fixed-sentence replies.)
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
4. **In the kiosk:** repeat step 5. The spoken answers now use the ElevenLabs voice, in all 5 languages. Spoken questions go to ElevenLabs Scribe, which hears which language you spoke: ask in Spanish or Vietnamese and the answer comes back in that language.
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

**How it works:** someone texts your Twilio number. Twilio forwards the text to your laptop's `/sms` page, and whatever the app answers, Twilio texts back. Your laptop isn't reachable from the internet, so **ngrok** gives it a temporary public web address that Twilio can call. The app needs **no Twilio keys**. The only setup is telling Twilio where to forward texts.

You'll have three terminal windows open: the server, ngrok, and one spare.

### 11.1 Install and start ngrok (one time)

1. Make a free account at https://dashboard.ngrok.com/signup.
2. Install it on your Mac: `brew install ngrok`. If you don't have Homebrew, download ngrok from https://ngrok.com/download instead.
3. In the ngrok dashboard, open **Your Authtoken**, copy the command it shows, and run it once:
   ```bash
   ngrok config add-authtoken <your-token>
   ```
4. Make sure the server is running in its own terminal (`python -m uvicorn main:app --reload`).
5. In a **new** terminal window, run:
   ```bash
   ngrok http 8000
   ```
6. ngrok fills the window with a status screen. Find the **Forwarding** line, for example:
   ```
   Forwarding   https://a1b2-c3d4.ngrok-free.app -> http://localhost:8000
   ```
   Copy the `https://….ngrok-free.app` part. That's your public address. Leave this window open: closing it turns the address off.
7. **Check it:** open `https://<your-ngrok-address>/dashboard` in your browser. If ngrok shows a "You are about to visit" page, click **Visit Site**. You should see the dashboard. Twilio skips that page by itself.

> The free address **changes every time you restart ngrok**. When it changes, repeat step 11.3 with the new address.

### 11.2 Twilio account and number (one time)

1. Sign up at https://www.twilio.com/try-twilio and verify your email and your own phone number.
2. In the Twilio Console (https://console.twilio.com), go to **Phone Numbers → Manage → Active numbers**. A US trial account comes with one number already there, usually toll-free (8xx). Note it down.

### 11.3 Point the number at your laptop

1. **Phone Numbers → Manage → Active numbers** → click your number.
2. Open the **Configure** tab and scroll to **Messaging Configuration**.
3. Set **A message comes in** to **Webhook**. Paste `https://<your-ngrok-address>/sms` into the URL box (don't forget `/sms` at the end) and set the method to **HTTP POST**.
4. Click **Save configuration** at the bottom of the page.

### 11.4 Test it with Twilio's Virtual Phone (works tonight, no approval needed)

US trial numbers can't text real phones until Twilio approves the number (see 11.5). The **Virtual Phone** is a fake phone inside the Console that skips that approval, so use it for testing.

1. In the Console, open **Messaging → Virtual Phone** (or https://console.twilio.com/us1/develop/sms/virtual-phone).
2. Choose your Twilio number as the sender and send: `necesito comida cerca de UH`.
3. **Expect:** within a few seconds, a Spanish reply with a food place appears in the Virtual Phone chat. A pin also drops on the dashboard.
4. Look at the ngrok window. A line `POST /sms 200 OK` means Twilio reached your laptop.
5. Try the conversations from step 3: `2`, a follow-up like `is it open tomorrow?`, and `AYUDA`.

### 11.5 Texting from a real phone (optional, needs Twilio approval)

- A trial **toll-free** number must pass **Toll-Free Verification** before it can text US phones: Console → **Phone Numbers → Regulatory Compliance → Toll-Free Verifications**. It's free, but approval can take days, so start it early if you want this for the demo.
- After approval, a trial account can only text **verified** numbers. Add each phone under **Phone Numbers → Manage → Verified Caller IDs** (yours is already there). Replies start with "Sent from your Twilio trial account".
- Upgrading to a paid account removes the "trial" text and the verified-numbers limit, but US numbers still need registration (toll-free verification or A2P 10DLC).
- When it's approved: text the Twilio number from your phone with `necesito comida cerca de UH` and expect the same reply as in 11.4.

### 11.5b Real phones tonight: WhatsApp Sandbox (no approval needed)

Twilio's WhatsApp Sandbox lets real phones reach the app right away. The app answers WhatsApp exactly like SMS, so no code changes are needed.

1. In the Twilio Console, open **Messaging → Try it out → Send a WhatsApp message**. Accept the terms if asked.
2. The page shows the sandbox number (**+1 415 523 8886**) and a code like `join purple-tiger`.
3. On each phone that will test, open WhatsApp and send that exact `join …` message to +1 415 523 8886. Twilio replies that you've joined. A QR code on the same page does the same thing.
4. On the same Console page, open **Sandbox settings**. In **When a message comes in**, paste `https://<your-ngrok-address>/sms`, set the method to **POST**, and click **Save**.
5. From a joined phone, send `necesito comida cerca de UH` on WhatsApp.
6. **Expect:** a Spanish reply within a few seconds, and a pin on the dashboard. `2`, follow-ups and `AYUDA` work the same as SMS.

Notes:
- Each phone stays joined for **3 days**. After that, send the `join …` message again.
- Trial accounts include a limited number of free WhatsApp messages (about 100).
- **Demo tip:** put the join message and the sandbox number on a slide or a QR code so judges can try it from their own phones.

### 11.6 If texting doesn't work

| What you see | Fix |
|---|---|
| No reply, and nothing new in the ngrok window | Twilio isn't reaching your laptop. Recheck the webhook URL in 11.3: the current ngrok address plus `/sms`, set to HTTP POST, and saved |
| ngrok shows `POST /sms 404` | The URL is missing `/sms` or has a typo |
| ngrok shows `POST /sms 502` | The server isn't running. Start it again in its own terminal |
| `ERR_NGROK_...` in the ngrok window | Run the `ngrok config add-authtoken` step again |
| Twilio Console → **Monitor → Logs → Messaging** shows error 11200 | Twilio couldn't load your URL: ngrok or the server is down, or the address changed |
| Error 30032 / 30034 or "unverified" in the logs (real phone only) | The number isn't approved for US texting yet (11.5). Use the Virtual Phone or WhatsApp (11.5b) |
| WhatsApp: no reply | Check that the phone sent the `join …` message in the last 3 days, and that the sandbox webhook (11.5b step 4) has the current ngrok address |
| You can see every request and reply in detail | Open http://127.0.0.1:4040 while ngrok is running |

## 12. Real places: check the data (do this before the demo)

`data/resources.json` now holds 96 real Houston places, gathered from official pages and a few directories. Every place has `needs_review: true` until someone on the team checks it. The 12 old sample places moved to `tests/fixtures/` and are used only by the tests.

Run these on your Mac (the cloud setup can't reach these sites):

1. **Map locations:** `python scripts/geocode.py`. This re-checks every address with the US Census geocoder. If you add a place the geocoder can't find, type their `lat`/`lng` in by hand and set `"coords_source": "manual"`. Places without a location are skipped by the app.
2. **No made-up facts:** `python scripts/validate.py --check-sources`. This downloads every `source_url` and lists every hours or eligibility quote that isn't on that page. **Expect:** some quotes fail because the research tool shortened or reformatted them. For each one, open the page and fix the quote to the exact words, or delete the rule.
3. **Spot check:** open each place's `source_url`, compare name, address, phone, hours and rules, then set `"needs_review": false` and `"last_verified": "2026-10-11"` (today's date). Start with the places near UH (zip 77004), since the demo station is there.
4. Load into Tiger Data: `python seed_db.py`.

## 13. Eligibility questions

The app only asks about rules that the place's own page states. A place with no rules gets no questions.

1. Find a place with rules: `python -c "import json;[print(p['name'],[r['type'] for r in p['eligibility']]) for p in json.load(open('data/resources.json')) if p['eligibility']]"`.
2. Text-style test (Covenant House only takes ages 18–24):
   ```bash
   curl -s localhost:8000/ask -H 'content-type: application/json' -d '{"channel":"sms","session_id":"e1","text":"I need a shelter near 77006"}'
   ```
   **Expect:** if the top places have rules, the reply is a yes/no question and `"asking"` is not null. Answer with `yes`, `no`, `skip` (or `sí`, `không`, `不知道`…) as the next `text` with the same `session_id`. A "no" removes places that need that rule. At most 3 questions, then the answer.
3. In the kiosk, a question shows `Yes or No?` on the LCD. **Short press = yes, long press = no.**

## 14. Bus directions (METRO)

1. Load METRO's schedule into Tiger Data once (needs `DATABASE_URL`): `python scripts/load_gtfs.py`. Without a database you can save the stops near the station instead: `python scripts/load_gtfs.py --local 29.7199 -95.3422`.
2. Ask for a place, then `how do I get there?` (or `bus`, `¿cómo llego?`).
3. **Expect:** "Take bus 54 from … at 4:12 PM, ride 6 stops …" in the person's language, and `"transit"` filled in the JSON. The kiosk LCD alternates `Option 1/3 0.9mi` and `Bus 54 4:12PM`. With no bus found: "no direct bus right now" plus the distance.

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
