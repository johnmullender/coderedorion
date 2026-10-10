// 1602 I2C LCD test for Arduino UNO R4 WiFi, no library and no extra resistors.
// Talks I2C "by hand" on A4/A5 using the board's built-in pull-ups.
// Wiring: GND->GND, VCC->5V, SDA->A4, SCL->A5. Open Serial Monitor at 9600.

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

void setup() {
  Serial.begin(9600);
  delay(1500);
  sdaHigh(); sclHigh();

  // Look for the LCD at the usual addresses (0x20-0x27 and 0x38-0x3F)
  for (byte a = 0x20; a <= 0x3F; a++) {
    i2cStart();
    bool ok = i2cWrite(a << 1);
    i2cStop();
    if (ok) { lcdAddr = a; break; }
  }

  if (lcdAddr == 0) {
    Serial.println("LCD not found - check the 4 wires");
    return;
  }
  Serial.print("LCD found at 0x");
  Serial.println(lcdAddr, HEX);

  lcdInit();
  lcdGoto(0, 0);
  lcdPrint("HelpLine works!");
  lcdGoto(0, 1);
  lcdPrint("R4, no resistors");
}

void loop() {}
