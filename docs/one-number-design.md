# HelpLine Houston: project brief

> **For a new chat or teammate:** this file is the source of truth for *what* we're building at CodeRED Orion. Read it top to bottom, then read `STATUS.md` for where we're at.

---

## 0. Status

Progress lives in **`STATUS.md`**. This file is the plan; that one is where we're at.

---

## 1. The event

- **Hackathon:** CodeRED Orion, University of Houston, Student Center South.
- **Hacking:** Sat Oct 10 1:00 PM → **Sun Oct 11 1:00 PM** (hard stop). Judging 1:30–3:30 PM Sunday.
- **Tracks we're targeting:**
  - **Best Overall ($1000):** ambitious problem, big impact and reach, *plus* a complete and stable build. Primary target.
  - **Best Pitch ($500):** target users, why we beat existing options, how it survives after the hackathon.
  - **Best Hardware (43" TV):** hardware and software integrated. Secondary, since our hardware is modest.
  - Best Environment ($500): only if heat/storm mode ships.
- **Useful freebies:** Lovable Pro (code `COMM-CODERED-T5PX` at lovable.dev) to scaffold the dashboard. A Google AI Studio workshop (5 PM Sat, Heights Room) covers the free Gemini API key.

---

## 2. What we're building

**One line:** text one number, in any language, and get the single place in Houston that can help you *right now*: food, shelter, clinics, libraries, cooling centers. No app, no data plan, works on a flip phone.

**Three pieces:**
1. **SMS line (the core):** a Twilio number. You text what you need, and it replies with the nearest place that's open now and that you qualify for, in your language.
2. **Help station:** a voice kiosk for libraries and bus stops, for people with no phone. The front is just the 1602 LCD, button, knob and buzzer, with a mic and speaker; a hidden laptop is the brain (no screen showing). It does everything the text line does, by voice: press the button, ask anything you could text (including follow-ups), hear the answer and see the place and address on the LCD.
3. **Live dashboard:** a map of requests coming in and which needs went unmet. This is the "data for the city" angle for the pitch.

**Positioning vs 211:** we're the *instant front door* to 211, not a replacement. 211 Texas live operators work Mon–Fri 8am–6pm, callers report 30–60+ min holds, the phone menu is confusing, and texting isn't available statewide (a 2023 Texas bill proposed adding it). We answer in seconds, 24/7, by text, in any language. Complex cases get handed to 211.
Sources: [Houston Case Managers 211 guide](https://houstoncasemanagers.com/211-texas) (reader reports 2022–23), [TX HB 2473 bill analysis](https://capitol.texas.gov/tlodocs/88R/analysis/pdf/HB02473H.pdf). *Check whether 211 texting has launched before saying it on stage.*

---

## 3. How the text line works

Example:
```
User:  necesito comida hoy, estoy cerca de UH
Bot:   Lo más cercano abierto ahora:
       🍎 Despensa St. Mark, 0.9 mi. Abierto hasta 6pm. Traiga identificación.
       1234 Calle X  📍 maps.link/abc
       Responda 2 para otra opción, o AYUDA para hablar con alguien.
```

Pipeline (one endpoint serves SMS and the kiosk):
```
POST /ask {channel: "sms"|"kiosk", from, text, lat?, lng?}
 1. Understand  LLM → strict JSON {need, language, location_text, constraints}
 2. Locate      zip/landmark → lat/lng (zip centroid table or geocoding API)
 3. Match       PLAIN CODE: category == need AND open_now AND eligible,
                ranked by distance (closing soon = penalty)
 4. Answer      LLM rewrites the top DB row into ≤300 chars in the user's language,
                using ONLY fields from that row
 5. Log         requests table → dashboard
```
- **The AI never chooses the place.** Code picks from verified rows, so nothing gets made up. This is our answer to "what about hallucinations?"
- **Conversation state:** keep the conversation (last result list + recent messages) per session for 30 minutes so "2", "more", "is it far?" or "what about a shelter instead?" all work. An SMS session is keyed by phone number; a kiosk session starts on the first button press and ends at the idle timeout.
- **"HELP"/"AYUDA"** replies with the 211 number and the provider's phone.

**Resource record:**
```json
{
  "id": "st-mark-pantry", "name": "St. Mark Food Pantry",
  "category": ["food"], "address": "...", "lat": 29.72, "lng": -95.34,
  "hours": {"mon": ["09:00-12:00"], "wed": ["13:00-18:00"]},
  "requirements": {"id_required": true, "proof_of_income": false, "zip_restricted": []},
  "languages": ["en","es"], "phone": "...", "source_url": "...", "last_verified": "2026-10-10"
}
```
**Target:** 30–50 real rows in UH + Third Ward first (Midtown if time) across food, shelter, hygiene, free clinics, libraries, cooling centers and legal aid. Sources: Houston Food Bank finder, Coalition for the Homeless, Houston Public Library, City of Houston cooling centers.

**Stack:** FastAPI or Express · SQLite or Supabase · Twilio (trial: verify every teammate's phone, plus one for the demo) · Gemini or Claude API · Leaflet map dashboard (Lovable can scaffold it).

---

## 4. The help station

**Same brain as the text line.** The help station is just another channel into `POST /ask` (channel `"kiosk"`, fixed lat/lng of the station). Anything you can text, you can say: the first question, follow-ups ("is it open tomorrow?", "anything closer?", "what about a clinic?"), "next option", in any supported language. The only difference is input (speech-to-text) and output (spoken + shown on the LCD).

**Parts:** Arduino UNO R4 WiFi + Grove Base Shield, Grove Button, Grove Rotary Angle Sensor, Grove Buzzer, Inland 1602 I2C LCD, laptop (mic, speakers, screen).

**Wiring:** Button → D2 · Buzzer → D4 · Rotary → A0 (3 Grove cables). LCD via 4 M/F jumpers: GND→GND, VCC→5V, SDA→SDA/A4, SCL→SCL/A5. Shield voltage switch on **5V**.

**Each part's job:**
| Part | Job |
|---|---|
| Button | **Short press** = start speaking (press again or 6s of silence ends it). **Long press (2s)** = announce options out loud: when idle, it reads what you can ask about ("food, shelter, showers, clinics, libraries, cooling centers, legal help"); after an answer, it reads all current results ("Option 1: St. Mark Pantry, 0.9 miles, open till 6. Option 2: ...") |
| Knob | Before speaking: choose language (EN / ES / VI / ZH / AR). After an answer: scroll through options 1 → 2 → 3 (same as texting "2") |
| Buzzer | Audio cues for low-vision users: 1 beep = listening, 2 beeps = answer ready, low tone = error |
| LCD (16x2) | Row 1 = state ("Press for help", "Listening...", "Thinking...", "Option 1/3"). Row 2 = language while turning the knob, then a 16-char summary like "St.Mark 0.9mi 6p". Driven by hand-rolled I2C on A4/A5 with internal pull-ups (code in `lcd-r4-no-resistors.ino`, auto-detects address 0x20–0x3F). **No `Wire` / LiquidCrystal_I2C** on our UNO R4 WiFi. Keep the shield's I2C Grove ports empty |
| Laptop (hidden) | Chrome kiosk page: mic → speech-to-text → `POST /ask` → text-to-speech through the speaker, and sends the place + address to the LCD over Web Serial. External USB mic / speaker optional |

**Flow:** idle LCD "Press for help" → turn knob for language → short press → 1 beep, speak → answer spoken and shown, 2 beeps → keep talking (short press again) for follow-ups, turn the knob to browse, or long press to hear all options → 60s idle ends the session.

**LCD rule (UNO R4 WiFi):** drive the LCD with the hand-rolled I2C code from `lcd-r4-no-resistors.ino`. Never call `Wire.begin()` or use LiquidCrystal_I2C (they don't work on our board and blank the screen). Keep the shield's I2C Grove ports empty. One sketch per sketch folder (the IDE compiles all .ino tabs together).

**Software split:** the Arduino is dumb I/O over USB serial. It sends `BTN`, `HOLD`, `KNOB:<0-1023>` and accepts `LCD1:<text>`, `LCD2:<text>`, `BEEP:<n>`, `ERR`. The kiosk page talks to it via Web Serial in Chrome (or a small Python `pyserial` bridge).

**Hardware test:** `hardware-test.ino` checks LCD, button (short/long press), knob and buzzer in one sketch. Its LCD + button code is the starting point for the real station firmware.

---

## 5. Build order and schedule

See **`tech-plan.md`** for stack decisions, the ordered task list and milestones M1–M6.

---|---|
| 2:50–4:00 PM | LCD test. Repo, Twilio, LLM key. Agree on the resource JSON schema |
| 4:00–7:00 PM | Data: curate rows. Backend: SMS loop with fake data, then real data. Frontend: dashboard + kiosk page. Hardware: Arduino serial protocol + buzzer/knob/button |
| **7:00 PM checkpoint 1** | A real text gets a real answer from real data. If not, cut voice calls and the heatmap |
| 7:00 PM–1:00 AM | Multilingual, open-now ranking, conversation state, kiosk end to end, dashboard live |
| **1:00 AM checkpoint 2** | Full demo works once. Tag a known-good commit |
| 1:00–6:00 AM | Stretch features. Sleep in shifts; one person always on stability |
| 6:00–10:00 AM | **Feature freeze at 10.** Test 20 messy messages (typos, Spanish, Vietnamese, no zip, "im hungry") |
| 10:00 AM–12:30 PM | Devpost, 2-min backup video, slides, rehearse 3× |
| 12:30 PM | Submit: Best Overall, Best Pitch, Best Hardware (+ Environment if heat mode shipped) |

---

## 6. Demo script (2 minutes)

1. **Problem (20s):** "It's 9 PM, you're out of food, you have a prepaid phone with no data. Today you'd call 211 and hold, if it's even open."
2. **Text (30s):** a judge texts the number in Spanish. The answer lands on their phone and on the big screen, and a pin drops on the dashboard.
3. **Help station (40s):** a second judge turns the knob to Vietnamese, presses the button and asks for a place to sleep tonight. They hear the answer, ask a follow-up ("is it far?") and long-press to hear all the options while the LCD shows the address.
4. **Dashboard (20s):** "Every request is a data point. The city sees unmet needs by neighborhood and hour."
5. **Close (10s):** "One number. Any language. Any phone. The help that's open right now."

---

## 7. Judge Q&A

- **AI hallucinations?** The AI can't choose a place. Code picks from verified rows; the AI only rewords that row.
- **Data freshness?** Every row has `last_verified`. In production we'd pull 211's own data via the Open Referral HSDS standard and let providers text daily open/closed status.
- **Privacy?** No accounts. Phone numbers are hashed in logs and chat state is deleted after 30 minutes. The help station stores nothing.
- **Isn't this just 211?** It's the instant, text-first, multilingual front door that hands complex cases to 211.
- **Business model?** Free for users. Funded by United Way, the city or hospital systems (cheaper than ER visits). Help station licenses plus the unmet-needs dashboard. Twilio costs about a cent per text.
- **Other cities?** HSDS is a national format, so any city's 211 data drops in.
