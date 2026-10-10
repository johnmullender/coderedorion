# HelpLine Houston: Status

**Last updated:** Sat Oct 10, 6:10 PM
**What we're building / how:** see `one-number-design.md`

---

## Hardware
- **Done:** Arduino UNO R4 WiFi + Grove Base Shield. Inland 1602 I2C LCD working on A4/A5 via hand-rolled I2C (`lcd-r4-no-resistors.ino`). Button (short and long press), knob and buzzer verified with `hardware-test.ino`.
- **In progress:** —
- **Blockers:** —
- **Next:** Write the station firmware: serial protocol to the laptop (`BTN`, `HOLD`, `KNOB:<n>` out; `LCD1:`, `LCD2:`, `BEEP:<n>` in), built from `hardware-test.ino`.

## Resource data
- **Done:** Schema defined (see brief, section 3).
- **In progress:** —
- **Blockers:** —
- **Next:** Curate 60–100 real Houston places (food, shelter, hygiene, clinics, libraries, cooling centers, legal aid) near UH, Third Ward, Downtown and Gulfton into `resources.json`.

## Text line (SMS)
- **Done:** —
- **In progress:** —
- **Blockers:** Repo not set up yet.
- **Next:** Repo, Twilio trial number (verify teammates' phones), LLM key, then `POST /ask`: parse → locate → match → reply, end to end with fake data.

## Help station (kiosk)
- **Done:** Hardware verified (see Hardware).
- **In progress:** —
- **Blockers:** Needs `/ask` from the text line.
- **Next:** Kiosk web page: speech-to-text → `/ask` (channel `kiosk`) → text-to-speech, full answer + QR on screen, talks to the Arduino over Web Serial.

## Dashboard
- **Done:** —
- **In progress:** —
- **Blockers:** Needs the requests log from `/ask`.
- **Next:** Leaflet map with live request pins.

## Pitch & submission
- **Done:** Demo script and judge Q&A drafted (brief, sections 6–7).
- **In progress:** —
- **Blockers:** —
- **Next:** Devpost page, slides, 2-minute backup video (after feature freeze).

---

## Stretch (only after the core works)
- [ ] Voice calls on the same number
- [ ] Heat / storm mode (cooling centers and shelters; unlocks Best Environment)
- [ ] NFC sticker tap-to-take (static link → `/station/1`)
- [ ] Unmet-needs heatmap
