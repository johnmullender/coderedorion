// Help station hardware test: LCD + button + knob + buzzer, all at once.
// Arduino UNO R4 WiFi + Grove Base Shield (switch on 5V).
// Wiring: LCD GND->GND, VCC->5V, SDA->A4, SCL->A5 (jumpers)
//         Grove Button -> D2 port, Grove Buzzer -> D4 port, Grove Rotary -> A0 port
// LCD uses hand-rolled I2C (same code as lcd-r4-no-resistors.ino). Do NOT use Wire/LiquidCrystal_I2C.

const int BUTTON_PIN = 2;
const int BUZZER_PIN = 4;
const int KNOB_PIN = A0;
const unsigned long LONG_PRESS_MS = 2000;

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

void beep(int times) {
  for (int i = 0; i < times; i++) {
    digitalWrite(BUZZER_PIN, HIGH); delay(120);
    digitalWrite(BUZZER_PIN, LOW);  delay(120);
  }
}

const char* LANGS[] = {"English", "Espanol", "Tieng Viet", "Zhongwen", "Arabic"};
int lastLang = -1;
bool wasPressed = false;
bool longFired = false;
unsigned long pressStart = 0;

void setup() {
  Serial.begin(9600);
  pinMode(BUTTON_PIN, INPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  delay(1500);
  sdaHigh(); sclHigh();

  for (byte a = 0x20; a <= 0x3F; a++) {
    i2cStart();
    bool ok = i2cWrite(a << 1);
    i2cStop();
    if (ok) { lcdAddr = a; break; }
  }
  if (lcdAddr == 0) Serial.println("LCD not found");
  else { lcdInit(); lcdLine(0, "Hardware test"); }

  beep(1);   // startup beep = buzzer works
  Serial.println("Turn the knob, short-press and long-press the button");
}

void loop() {
  // Knob: 0-1023 split into 5 languages
  int raw = analogRead(KNOB_PIN);
  int lang = map(raw, 0, 1024, 0, 5);
  if (lang != lastLang) {
    lastLang = lang;
    char line[17];
    snprintf(line, sizeof(line), "%d %s", raw, LANGS[lang]);
    if (lcdAddr) lcdLine(1, line);
    Serial.print("Knob "); Serial.print(raw); Serial.print(" -> "); Serial.println(LANGS[lang]);
  }

  // Button: short press vs long press (2s)
  bool pressed = digitalRead(BUTTON_PIN) == HIGH;
  if (pressed && !wasPressed) { pressStart = millis(); longFired = false; }
  if (pressed && !longFired && millis() - pressStart >= LONG_PRESS_MS) {
    longFired = true;
    if (lcdAddr) lcdLine(0, "LONG press");
    Serial.println("LONG press");
    beep(2);
  }
  if (!pressed && wasPressed && !longFired) {
    if (lcdAddr) lcdLine(0, "SHORT press");
    Serial.println("SHORT press");
    beep(1);
  }
  wasPressed = pressed;
  delay(20);
}
