// HelpLine Houston: help station firmware.
// Board: Arduino UNO R4 WiFi + Grove Base Shield (switch on 5V).
//
// WIRING
//   1602 I2C LCD:  GND->GND, VCC->5V, SDA->A4, SCL->A5 (jumpers on the shield)
//   Grove Button:  D2 port   (reads HIGH while pressed)
//   Grove Buzzer:  D4 port   (active buzzer: HIGH = sound, LOW = silent)
//   Grove Rotary:  A0 port   (knob, 0-1023)
//
// The LCD uses hand-rolled I2C (same code as hardware-test.ino).
// Do NOT use Wire, Wire.begin() or LiquidCrystal_I2C: they blank the screen on this board.
// This sketch includes no libraries on purpose.
//
// SERIAL PROTOCOL (9600 baud, one line per message, each line ends with \n)
//   Arduino -> laptop:
//     READY            once, after boot
//     BTN              short press (released before 2 seconds)
//     HOLD             long press: sent once when 2 seconds is reached while still held
//     KNOB:<0-1023>    once at boot, then whenever the knob moves by more than 15
//   Laptop -> Arduino:
//     LCD1:<text>      top row, 16 characters max
//     LCD2:<text>      bottom row, 16 characters max, stops any scrolling
//     SCROLL2:<text>   bottom row, scrolls right-to-left if longer than 16 characters
//     BEEP:<n>         n short beeps, n = 1 to 5
//     ERR              one long tone (about 600 ms; the buzzer is not pitched)
//
// NON-BLOCKING DESIGN
//   loop() never waits with delay(). Each feature remembers the millis() time of its
//   next step and checks it on every pass through loop(). That keeps the button,
//   knob, serial input and scrolling all working at the same time.

// ---------- PINS AND TIMING ----------
const int BUTTON_PIN = 2;
const int BUZZER_PIN = 4;
const int KNOB_PIN = A0;

const unsigned long DEBOUNCE_MS = 30;        // button must stay steady this long
const unsigned long LONG_PRESS_MS = 2000;    // holding this long sends HOLD
const int KNOB_CHANGE_MIN = 15;              // send KNOB only when it moves more than this
const unsigned long SCROLL_STEP_MS = 300;    // time between scroll steps
const unsigned long BEEP_ON_MS = 120;        // one beep length
const unsigned long BEEP_OFF_MS = 120;       // silence between beeps
const unsigned long ERR_TONE_MS = 600;       // length of the error tone

const int CMD_LINE_MAX = 120;                    // longest incoming line (not counting the end mark)
const int SCROLL_MAX = 140;                  // room for scrolled text + gap + end mark
const int SCROLL_GAP = 4;                    // spaces between the end and the start of the text

// ---------- LCD: hand-rolled I2C (copied exactly from hardware-test.ino) ----------
const int SDA_PIN = A4;
const int SCL_PIN = A5;
const byte BL = 0x08, EN = 0x04, RS = 0x01;
byte lcdAddr = 0;

void sdaHigh() { pinMode(SDA_PIN, INPUT_PULLUP); }
void sdaLow()  { pinMode(SDA_PIN, OUTPUT); digitalWrite(SDA_PIN, LOW); }
void sclHigh() { pinMode(SCL_PIN, INPUT_PULLUP); }
void sclLow()  { pinMode(SCL_PIN, OUTPUT); digitalWrite(SCL_PIN, LOW); }
void wait()    { delayMicroseconds(20); }

void i2cStart() { sdaHigh(); sclHigh(); wait(); sdaLow(); wait(); sclLow(); wait(); }
void i2cStop()  { sdaLow(); wait(); sclHigh(); wait(); sdaHigh(); wait(); }

bool i2cWrite(byte b) {
  for (int i = 7; i >= 0; i--) {
    if (b & (1 << i)) sdaHigh(); else sdaLow();
    wait(); sclHigh(); wait(); sclLow(); wait();
  }
  sdaHigh(); wait(); sclHigh(); wait();
  bool ack = (digitalRead(SDA_PIN) == LOW);
  sclLow(); wait();
  return ack;
}

bool pcfWrite(byte data) {
  i2cStart();
  bool ok = i2cWrite(lcdAddr << 1);
  if (ok) ok = i2cWrite(data);
  i2cStop();
  return ok;
}

void pulse(byte data) {
  pcfWrite(data | EN | BL);
  delayMicroseconds(1);
  pcfWrite((data & ~EN) | BL);
  delayMicroseconds(50);
}

void write4(byte nibble, byte mode) { pulse((nibble & 0xF0) | mode); }
void send(byte value, byte mode)    { write4(value & 0xF0, mode); write4((value << 4) & 0xF0, mode); }
void cmd(byte c)                    { send(c, 0); }
void lcdPrint(const char* s)        { while (*s) send(*s++, RS); }
void lcdGoto(byte col, byte row)    { cmd(0x80 | (col + (row ? 0x40 : 0))); }

void lcdInit() {
  delay(50);
  pcfWrite(BL); delay(10);
  write4(0x30, 0); delay(5);
  write4(0x30, 0); delay(5);
  write4(0x30, 0); delay(1);
  write4(0x20, 0);              // switch to 4-bit mode
  cmd(0x28);                    // 2 lines, 5x8 font
  cmd(0x0C);                    // display on, cursor off
  cmd(0x01); delay(2);          // clear
  cmd(0x06);                    // move cursor right after each char
}


void lcdLine(byte row, const char* text) {
  char buf[17];
  snprintf(buf, sizeof(buf), "%-16s", text);   // pad to 16 chars so old text is erased
  lcdGoto(0, row);
  lcdPrint(buf);
}
// ---------- END OF COPIED LCD CODE ----------

// Only draw on the LCD if one was found at boot.
void showRow(byte row, const char* text) {
  if (lcdAddr) lcdLine(row, text);
}

// ---------- SMALL HELPERS (no libraries) ----------

// True if the string s starts with the text in prefix.
bool startsWith(const char* s, const char* prefix) {
  while (*prefix) {
    if (*s != *prefix) return false;
    s++;
    prefix++;
  }
  return true;
}

// True if the two strings are exactly the same.
bool sameText(const char* a, const char* b) {
  while (*a && *a == *b) {
    a++;
    b++;
  }
  return *a == *b;
}

// True once the millis() time `when` has arrived.
// The subtraction trick still works after millis() wraps around (about every 49 days).
bool timeReached(unsigned long when) {
  return (long)(millis() - when) >= 0;
}

// ---------- BUTTON STATE ----------
bool rawButton = false;              // what the pin reads right now
bool buttonDown = false;             // the debounced (trusted) state
unsigned long lastRawChange = 0;     // when rawButton last changed
unsigned long pressStart = 0;        // when the current press began
bool holdSent = false;               // true once HOLD was sent for this press

// ---------- KNOB STATE ----------
int lastKnobSent = 0;                // knob value last sent to the laptop

// ---------- SCROLL STATE (row 2) ----------
bool scrolling = false;
char scrollBuf[SCROLL_MAX];          // the text plus the gap, ending with '\0'
int scrollLen = 0;                   // number of characters in scrollBuf (not counting '\0')
int scrollPos = 0;                   // index where the 16-char window starts
unsigned long nextScrollMs = 0;      // when to move the window next

// ---------- BUZZER STATE ----------
int beepsLeft = 0;                   // beeps still waiting to start
bool buzzerOn = false;               // true while the buzzer is sounding
unsigned long buzzerChangeAt = 0;    // when the buzzer should switch state next

// ---------- SERIAL INPUT STATE ----------
char lineBuf[CMD_LINE_MAX + 1];          // holds one incoming line
int lineLen = 0;                     // characters stored so far

// Show the 16-character window of the scrolling text at scrollPos.
void showScrollWindow() {
  char win[17];
  for (int i = 0; i < 16; i++) {
    win[i] = scrollBuf[(scrollPos + i) % scrollLen];   // % wraps back to the start
  }
  win[16] = '\0';
  showRow(1, win);
}

// Handle SCROLL2:<text>. Short text is shown static, long text starts scrolling.
void startScroll(const char* text) {
  int n = 0;
  while (text[n] != '\0') n++;

  if (n <= 16) {
    scrolling = false;
    showRow(1, text);
    return;
  }

  // Copy the text, then add the gap so the end and the start are separated.
  int k = 0;
  for (int i = 0; i < n && k < SCROLL_MAX - SCROLL_GAP - 1; i++) {
    scrollBuf[k++] = text[i];
  }
  for (int g = 0; g < SCROLL_GAP; g++) {
    scrollBuf[k++] = ' ';
  }
  scrollBuf[k] = '\0';

  scrollLen = k;
  scrollPos = 0;
  scrolling = true;
  nextScrollMs = millis() + SCROLL_STEP_MS;
  showScrollWindow();                // show the first window right away
}

// Move the scroll window one character when its time comes.
void updateScroll() {
  if (!scrolling) return;
  if (!timeReached(nextScrollMs)) return;

  nextScrollMs = millis() + SCROLL_STEP_MS;
  scrollPos = (scrollPos + 1) % scrollLen;
  showScrollWindow();
}

// Queue n beeps. They play one after another in updateBuzzer().
void queueBeeps(int n) {
  // If the buzzer is idle, start the first beep right away. Resetting the time
  // here also keeps a very old buzzerChangeAt from confusing timeReached().
  if (!buzzerOn && beepsLeft == 0) buzzerChangeAt = millis();
  beepsLeft += n;
}

// Start the long error tone. This cancels any queued beeps.
void startErrorTone() {
  beepsLeft = 0;
  digitalWrite(BUZZER_PIN, HIGH);
  buzzerOn = true;
  buzzerChangeAt = millis() + ERR_TONE_MS;
}

// Turn the buzzer on or off step by step, without waiting.
void updateBuzzer() {
  if (!buzzerOn && beepsLeft == 0) return;    // idle: nothing to do
  if (!timeReached(buzzerChangeAt)) return;   // not time for the next step yet

  if (buzzerOn) {
    // The current beep or tone is over: go quiet for a moment.
    digitalWrite(BUZZER_PIN, LOW);
    buzzerOn = false;
    buzzerChangeAt = millis() + BEEP_OFF_MS;
  } else if (beepsLeft > 0) {
    // Start the next beep.
    beepsLeft--;
    digitalWrite(BUZZER_PIN, HIGH);
    buzzerOn = true;
    buzzerChangeAt = millis() + BEEP_ON_MS;
  }
}

// Handle one complete line from the laptop (no \r or \n at the end).
void handleLine(const char* line) {
  if (startsWith(line, "LCD1:")) {
    showRow(0, line + 5);
  } else if (startsWith(line, "LCD2:")) {
    scrolling = false;                 // a plain line stops any scrolling
    showRow(1, line + 5);
  } else if (startsWith(line, "SCROLL2:")) {
    startScroll(line + 8);
  } else if (startsWith(line, "BEEP:")) {
    // Read the number after "BEEP:". Then keep it between 1 and 5.
    int n = 0;
    for (const char* p = line + 5; *p >= '0' && *p <= '9'; p++) {
      if (n < 100) n = n * 10 + (*p - '0');
    }
    if (n < 1) n = 1;
    if (n > 5) n = 5;
    queueBeeps(n);
  } else if (sameText(line, "ERR")) {
    startErrorTone();
  }
  // Any other line is ignored.
}

// Read any bytes waiting on Serial. Build lines and handle each one when \n arrives.
void readSerial() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();

    if (c == '\r') {
      continue;                        // ignore carriage returns
    }
    if (c == '\n') {
      lineBuf[lineLen] = '\0';
      if (lineLen > 0) handleLine(lineBuf);
      lineLen = 0;
    } else if (lineLen < CMD_LINE_MAX) {
      lineBuf[lineLen++] = c;
    }
    // If the line is already full, the extra characters are dropped.
  }
}

// Check the button. Sends BTN on a short press and HOLD once at 2 seconds.
void updateButton() {
  bool reading = (digitalRead(BUTTON_PIN) == HIGH);

  // If the pin changed, restart the debounce timer.
  if (reading != rawButton) {
    rawButton = reading;
    lastRawChange = millis();
  }

  // Accept the new state only after it has stayed the same for DEBOUNCE_MS.
  if (rawButton != buttonDown && millis() - lastRawChange >= DEBOUNCE_MS) {
    buttonDown = rawButton;
    if (buttonDown) {
      // Just pressed. Remember when, so the hold time is measured from the real press.
      pressStart = lastRawChange;
      holdSent = false;
    } else if (!holdSent) {
      // Just released after less than 2 seconds: a short press.
      Serial.println("BTN");
    }
    // If HOLD was already sent, a release sends nothing.
  }

  // While still held, send HOLD once when 2 seconds have passed.
  if (buttonDown && !holdSent && millis() - pressStart >= LONG_PRESS_MS) {
    holdSent = true;
    Serial.println("HOLD");
  }
}

// Send KNOB:<raw> when the knob has moved more than KNOB_CHANGE_MIN since the last send.
void updateKnob() {
  int raw = analogRead(KNOB_PIN);
  int diff = raw - lastKnobSent;
  if (diff > KNOB_CHANGE_MIN || diff < -KNOB_CHANGE_MIN) {
    lastKnobSent = raw;
    Serial.print("KNOB:");
    Serial.println(raw);
  }
}

void setup() {
  Serial.begin(9600);
  pinMode(BUTTON_PIN, INPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, LOW);

  delay(1500);                         // give the LCD time to power up
  sdaHigh(); sclHigh();

  // Look for the LCD on I2C addresses 0x20 to 0x3F.
  for (byte a = 0x20; a <= 0x3F; a++) {
    i2cStart();
    bool ok = i2cWrite(a << 1);
    i2cStop();
    if (ok) { lcdAddr = a; break; }
  }

  if (lcdAddr == 0) {
    Serial.println("LCD not found");   // the protocol still runs without a screen
  } else {
    lcdInit();
    lcdLine(0, "Press for help");
    lcdLine(1, "English");
  }

  // Send the knob value once at boot.
  lastKnobSent = analogRead(KNOB_PIN);
  Serial.print("KNOB:");
  Serial.println(lastKnobSent);

  Serial.println("READY");
}

void loop() {
  readSerial();       // laptop commands
  updateButton();     // BTN and HOLD
  updateKnob();       // KNOB:<raw>
  updateScroll();     // row 2 scrolling
  updateBuzzer();     // beeps and error tone
}
