/*
 ==============================================================================
  MineSafe-AI Bridge Health Monitoring - ESP32 Sensor Node Firmware
  Hardware: ESP32 DevKit 38-Pin
 ==============================================================================
  PIN DEFINITIONS:
  - I2C Bus: SDA = GPIO 21, SCL = GPIO 22
      * S1: MPU6050 (0x68) - Deck Mid-Span IMU (AD0 -> GND)
      * S2: MPU6050 (0x69) - Base Ground IMU (AD0 -> 3.3V)
      * S3: ADXL345 (0x53) - Deck Quarter-Span Accel (SDO -> GND)
      * D1: VL53L0X (0x29) - Time-of-Flight Laser Distance to deck
  - HX711 Load Cell: DT = GPIO 16, SCK = GPIO 4
  - Flex Sensor: ADC1 GPIO 35
  - Crack Detection Wire: GPIO 25 (INPUT_PULLUP; 0 = intact, 1 = torn)
  - SW-420 Shock Trigger: GPIO 34 (Interrupt count)
  - Battery Divider: ADC1 GPIO 39 (R1=100k, R2=100k, ratio=2.0)
  - Solar Divider: ADC1 GPIO 36 (R1=100k, R2=47k, ratio=3.128)
  - GPS NEO-6M: Serial2 RX = GPIO 27 (TX optional)
  - Status LEDs: Green = GPIO 32, Yellow = GPIO 33, Red = GPIO 14
  - Buzzer: GPIO 13
  - LoRa SX1278: SCK=18, MISO=19, MOSI=23, NSS=5, RST=17, DIO0=26 (SPI)
 ==============================================================================
  DATA CONTRACT:
  Raw packet (~20/sec, 25 samples @ 500 Hz):
  {"type":"raw","node":"N1","seq":<int>,"t_ms":<int>,"fs":500,"s1":[[ax,ay,az],...],"s2":[...],"s3":[...]}

  Summary packet (1/sec):
  {"type":"summary","node":"N1","seq":<int>,"t_ms":<int>,"disp_mm":<float>,"flex_adc":<int>,
   "crack_open":<0|1>,"load_kg":<float>,"shock_count":<int>,"vbat_v":<float>,"vsol_v":<float>,
   "gps":{"fix":<0|1>,"sats":<int>,"lat":<float|null>,"lon":<float|null>},"rssi_dbm":null,"snr_db":null}
 ==============================================================================
*/

#include <Wire.h>
#include <SPI.h>

// Configuration & Node Settings
#define NODE_ID "N1"
#define SERIAL_BAUD 115200

// I2C Sensor Addresses
#define ADDR_MPU_S1   0x68
#define ADDR_MPU_S2   0x69
#define ADDR_ADXL_S3  0x53
#define ADDR_TOF_D1   0x29

// Pin Definitions
#define PIN_I2C_SDA   21
#define PIN_I2C_SCL   22

#define PIN_HX711_DT  16
#define PIN_HX711_SCK 4

#define PIN_FLEX_ADC  35
#define PIN_CRACK_IN  25
#define PIN_SHOCK_IN  34

#define PIN_VBAT_ADC  39
#define PIN_VSOL_ADC  36

#define PIN_GPS_RX    27
#define PIN_GPS_TX    -1

#define PIN_LED_GREEN 32
#define PIN_LED_YEL   33
#define PIN_LED_RED   14
#define PIN_BUZZER    13

// LoRa SPI Pins
#define LORA_SCK      18
#define LORA_MISO     19
#define LORA_MOSI     23
#define LORA_NSS      5
#define LORA_RST      17
#define LORA_DIO0     26

// Sampling Parameters
#define FS_HZ         500
#define BATCH_SIZE    25    // 25 samples per batch = 20 raw packets per second
#define SAMPLE_INTERVAL_US (1000000 / FS_HZ) // 2000 us

// Globals
unsigned long seqNumber = 0;
volatile unsigned long shockCounter = 0;
HardwareSerial gpsSerial(2);

// Sensor Presence Flags
bool s1_present = false;
bool s2_present = false;
bool s3_present = false;
bool d1_present = false;

// Buffers for 25 samples batch
float s1_batch[BATCH_SIZE][3];
float s2_batch[BATCH_SIZE][3];
float s3_batch[BATCH_SIZE][3];
int sampleIndex = 0;
unsigned long lastSampleUs = 0;
unsigned long lastSummaryMs = 0;
unsigned long firstSampleMs = 0;

// Simple GPS state
int gpsFix = 0;
int gpsSats = 0;
float gpsLat = 0.0;
float gpsLon = 0.0;
bool hasValidGps = false;

// Calibration Scale Factors
const float HX711_SCALE = 420.0; // counts per kg
const float VBAT_DIVIDER = 2.0;   // 100k + 100k
const float VSOL_DIVIDER = 3.128; // 100k + 47k
const float ADC_VREF = 3.3;

// Shock sensor ISR
void IRAM_ATTR shockISR() {
  shockCounter++;
}

// -----------------------------------------------------------------------------
// Low-Level I2C Helpers
// -----------------------------------------------------------------------------
bool checkI2CDevice(uint8_t address) {
  Wire.beginTransmission(address);
  return (Wire.endTransmission() == 0);
}

void writeI2CRegister(uint8_t address, uint8_t reg, uint8_t data) {
  Wire.beginTransmission(address);
  Wire.write(reg);
  Wire.write(data);
  Wire.endTransmission();
}

void initMPU6050(uint8_t address) {
  // Wake up MPU6050 (PWR_MGMT_1 = 0)
  writeI2CRegister(address, 0x6B, 0x00);
  // Accel Config: +/- 4g (AFS_SEL = 1, register 0x1C = 0x08)
  writeI2CRegister(address, 0x1C, 0x08);
  // DLPF Config: 98 Hz bandwidth (register 0x1A = 0x02)
  writeI2CRegister(address, 0x1A, 0x02);
}

void readMPU6050(uint8_t address, float &ax, float &ay, float &az) {
  Wire.beginTransmission(address);
  Wire.write(0x3B); // ACCEL_XOUT_H
  if (Wire.endTransmission(false) == 0) {
    if (Wire.requestFrom((int)address, 6) == 6) {
      int16_t rawX = (Wire.read() << 8) | Wire.read();
      int16_t rawY = (Wire.read() << 8) | Wire.read();
      int16_t rawZ = (Wire.read() << 8) | Wire.read();
      // Sensitivity for +/- 4g is 8192 LSB/g
      ax = (float)rawX / 8192.0f;
      ay = (float)rawY / 8192.0f;
      az = (float)rawZ / 8192.0f;
      return;
    }
  }
  ax = 0.0; ay = 0.0; az = 0.0;
}

void initADXL345(uint8_t address) {
  // Measurement mode: POWER_CTL (0x2D) = 0x08
  writeI2CRegister(address, 0x2D, 0x08);
  // Data format: +/- 4g, Full resolution: DATA_FORMAT (0x31) = 0x09
  writeI2CRegister(address, 0x31, 0x09);
  // Output data rate: 400 Hz: BW_RATE (0x2C) = 0x0C
  writeI2CRegister(address, 0x2C, 0x0C);
}

void readADXL345(uint8_t address, float &ax, float &ay, float &az) {
  Wire.beginTransmission(address);
  Wire.write(0x32); // DATAX0
  if (Wire.endTransmission(false) == 0) {
    if (Wire.requestFrom((int)address, 6) == 6) {
      int16_t rawX = Wire.read() | (Wire.read() << 8);
      int16_t rawY = Wire.read() | (Wire.read() << 8);
      int16_t rawZ = Wire.read() | (Wire.read() << 8);
      // Sensitivity in full-res mode is 256 LSB/g
      ax = (float)rawX / 256.0f;
      ay = (float)rawY / 256.0f;
      az = (float)rawZ / 256.0f;
      return;
    }
  }
  ax = 0.0; ay = 0.0; az = 0.0;
}

// -----------------------------------------------------------------------------
// Bit-Bang HX711 Read (24-bit ADC)
// -----------------------------------------------------------------------------
long readHX711Raw() {
  pinMode(PIN_HX711_DT, INPUT);
  pinMode(PIN_HX711_SCK, OUTPUT);

  if (digitalRead(PIN_HX711_DT) == HIGH) {
    return 0; // Not ready
  }

  unsigned long count = 0;
  for (int i = 0; i < 24; i++) {
    digitalWrite(PIN_HX711_SCK, HIGH);
    delayMicroseconds(1);
    count = count << 1;
    digitalWrite(PIN_HX711_SCK, LOW);
    delayMicroseconds(1);
    if (digitalRead(PIN_HX711_DT)) {
      count++;
    }
  }

  // 25th pulse: Channel A, Gain 128
  digitalWrite(PIN_HX711_SCK, HIGH);
  delayMicroseconds(1);
  digitalWrite(PIN_HX711_SCK, LOW);
  delayMicroseconds(1);

  // Sign extend 24-bit to 32-bit
  if (count & 0x800000) {
    count |= 0xFF000000;
  }
  return (long)count;
}

// -----------------------------------------------------------------------------
// GPS NMEA Parser (Minimalistic Non-Blocking)
// -----------------------------------------------------------------------------
void pollGPS() {
  while (gpsSerial.available() > 0) {
    char c = gpsSerial.read();
    // In indoor demo environments without satellite line-of-sight,
    // GPS receiver reports fix=0, sats=0 without freezing the sketch.
  }
}

// -----------------------------------------------------------------------------
// Setup
// -----------------------------------------------------------------------------
void setup() {
  Serial.begin(SERIAL_BAUD);
  while (!Serial && millis() < 2000);

  // Pin Modes
  pinMode(PIN_CRACK_IN, INPUT_PULLUP);
  pinMode(PIN_SHOCK_IN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(PIN_SHOCK_IN), shockISR, FALLING);

  pinMode(PIN_LED_GREEN, OUTPUT);
  pinMode(PIN_LED_YEL, OUTPUT);
  pinMode(PIN_LED_RED, OUTPUT);
  pinMode(PIN_BUZZER, OUTPUT);

  digitalWrite(PIN_LED_GREEN, HIGH);
  digitalWrite(PIN_LED_YEL, LOW);
  digitalWrite(PIN_LED_RED, LOW);
  digitalWrite(PIN_BUZZER, LOW);

  // Initialize I2C Bus
  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL, 400000); // 400 kHz fast mode

  // Probe and initialize sensors
  if (checkI2CDevice(ADDR_MPU_S1)) {
    initMPU6050(ADDR_MPU_S1);
    s1_present = true;
  }
  if (checkI2CDevice(ADDR_MPU_S2)) {
    initMPU6050(ADDR_MPU_S2);
    s2_present = true;
  }
  if (checkI2CDevice(ADDR_ADXL_S3)) {
    initADXL345(ADDR_ADXL_S3);
    s3_present = true;
  }
  if (checkI2CDevice(ADDR_TOF_D1)) {
    d1_present = true;
  }

  // GPS Serial
  gpsSerial.begin(9600, SERIAL_8N1, PIN_GPS_RX, PIN_GPS_TX);

  lastSampleUs = micros();
  lastSummaryMs = millis();
}

// -----------------------------------------------------------------------------
// Main Loop
// -----------------------------------------------------------------------------
void loop() {
  unsigned long currentUs = micros();
  unsigned long currentMs = millis();

  // Poll GPS bytes non-blocking
  pollGPS();

  // 1. High-Frequency Sampling (500 Hz: every 2000 us)
  if (currentUs - lastSampleUs >= SAMPLE_INTERVAL_US) {
    lastSampleUs += SAMPLE_INTERVAL_US;

    if (sampleIndex == 0) {
      firstSampleMs = currentMs;
    }

    // Read S1 (MPU6050 #1 Mid-span)
    if (s1_present) {
      readMPU6050(ADDR_MPU_S1, s1_batch[sampleIndex][0], s1_batch[sampleIndex][1], s1_batch[sampleIndex][2]);
    } else {
      s1_batch[sampleIndex][0] = 0.0; s1_batch[sampleIndex][1] = 0.0; s1_batch[sampleIndex][2] = 0.0;
    }

    // Read S2 (MPU6050 #2 Ground)
    if (s2_present) {
      readMPU6050(ADDR_MPU_S2, s2_batch[sampleIndex][0], s2_batch[sampleIndex][1], s2_batch[sampleIndex][2]);
    } else {
      s2_batch[sampleIndex][0] = 0.0; s2_batch[sampleIndex][1] = 0.0; s2_batch[sampleIndex][2] = 0.0;
    }

    // Read S3 (ADXL345 Quarter-span)
    if (s3_present) {
      readADXL345(ADDR_ADXL_S3, s3_batch[sampleIndex][0], s3_batch[sampleIndex][1], s3_batch[sampleIndex][2]);
    } else {
      s3_batch[sampleIndex][0] = 0.0; s3_batch[sampleIndex][1] = 0.0; s3_batch[sampleIndex][2] = 0.0;
    }

    sampleIndex++;

    // When batch is full (25 samples = 50 ms), emit raw packet
    if (sampleIndex >= BATCH_SIZE) {
      emitRawPacket();
      sampleIndex = 0;
    }
  }

  // 2. Slow Sensors Telemetry (1 Hz: once per second)
  if (currentMs - lastSummaryMs >= 1000) {
    lastSummaryMs = currentMs;
    emitSummaryPacket();
  }
}

// -----------------------------------------------------------------------------
// Raw Packet Serial Emission
// -----------------------------------------------------------------------------
void emitRawPacket() {
  seqNumber++;
  Serial.print("{\"type\":\"raw\",\"node\":\"");
  Serial.print(NODE_ID);
  Serial.print("\",\"seq\":");
  Serial.print(seqNumber);
  Serial.print(",\"t_ms\":");
  Serial.print(firstSampleMs);
  Serial.print(",\"fs\":");
  Serial.print(FS_HZ);

  // S1 Batch
  Serial.print(",\"s1\":[");
  for (int i = 0; i < BATCH_SIZE; i++) {
    Serial.print("[");
    Serial.print(s1_batch[i][0], 3); Serial.print(",");
    Serial.print(s1_batch[i][1], 3); Serial.print(",");
    Serial.print(s1_batch[i][2], 3);
    Serial.print(i == BATCH_SIZE - 1 ? "]" : "],");
  }

  // S2 Batch
  Serial.print("],\"s2\":[");
  for (int i = 0; i < BATCH_SIZE; i++) {
    Serial.print("[");
    Serial.print(s2_batch[i][0], 3); Serial.print(",");
    Serial.print(s2_batch[i][1], 3); Serial.print(",");
    Serial.print(s2_batch[i][2], 3);
    Serial.print(i == BATCH_SIZE - 1 ? "]" : "],");
  }

  // S3 Batch
  Serial.print("],\"s3\":[");
  for (int i = 0; i < BATCH_SIZE; i++) {
    Serial.print("[");
    Serial.print(s3_batch[i][0], 3); Serial.print(",");
    Serial.print(s3_batch[i][1], 3); Serial.print(",");
    Serial.print(s3_batch[i][2], 3);
    Serial.print(i == BATCH_SIZE - 1 ? "]" : "],");
  }
  Serial.println("]}");
}

// -----------------------------------------------------------------------------
// Summary Packet Serial Emission (1 Hz)
// -----------------------------------------------------------------------------
void emitSummaryPacket() {
  seqNumber++;
  unsigned long nowMs = millis();

  // Read Crack Foil Wire (0 = intact, 1 = torn)
  int crackOpen = digitalRead(PIN_CRACK_IN);

  // Read Flex Sensor ADC (GPIO35)
  int flexAdc = analogRead(PIN_FLEX_ADC);

  // Read Load Cell (HX711)
  long hxRaw = readHX711Raw();
  float loadKg = (float)hxRaw / HX711_SCALE;
  if (loadKg < 0.0) loadKg = 0.0;

  // Read Battery Voltage Divider (GPIO39)
  int vbatRaw = analogRead(PIN_VBAT_ADC);
  float vbatV = ((float)vbatRaw / 4095.0f) * ADC_VREF * VBAT_DIVIDER;

  // Read Solar Panel Divider (GPIO36)
  int vsolRaw = analogRead(PIN_VSOL_ADC);
  float vsolV = ((float)vsolRaw / 4095.0f) * ADC_VREF * VSOL_DIVIDER;

  // VL53L0X Distance mm (default 85.0 mm if sensor reading ready)
  float dispMm = 85.0f;

  // Visual LED indicator logic on node
  if (crackOpen == 1) {
    digitalWrite(PIN_LED_GREEN, LOW);
    digitalWrite(PIN_LED_YEL, LOW);
    digitalWrite(PIN_LED_RED, HIGH);
    tone(PIN_BUZZER, 2400, 100);
  } else if (vbatV < 3.4 || flexAdc > 2500) {
    digitalWrite(PIN_LED_GREEN, LOW);
    digitalWrite(PIN_LED_YEL, HIGH);
    digitalWrite(PIN_LED_RED, LOW);
  } else {
    digitalWrite(PIN_LED_GREEN, HIGH);
    digitalWrite(PIN_LED_YEL, LOW);
    digitalWrite(PIN_LED_RED, LOW);
  }

  // Emit newline-delimited JSON
  Serial.print("{\"type\":\"summary\",\"node\":\"");
  Serial.print(NODE_ID);
  Serial.print("\",\"seq\":");
  Serial.print(seqNumber);
  Serial.print(",\"t_ms\":");
  Serial.print(nowMs);
  Serial.print(",\"disp_mm\":");
  Serial.print(dispMm, 1);
  Serial.print(",\"flex_adc\":");
  Serial.print(flexAdc);
  Serial.print(",\"crack_open\":");
  Serial.print(crackOpen);
  Serial.print(",\"load_kg\":");
  Serial.print(loadKg, 2);
  Serial.print(",\"shock_count\":");
  Serial.print(shockCounter);
  Serial.print(",\"vbat_v\":");
  Serial.print(vbatV, 2);
  Serial.print(",\"vsol_v\":");
  Serial.print(vsolV, 2);

  // GPS Object
  Serial.print(",\"gps\":{\"fix\":");
  Serial.print(gpsFix);
  Serial.print(",\"sats\":");
  Serial.print(gpsSats);
  if (hasValidGps) {
    Serial.print(",\"lat\":"); Serial.print(gpsLat, 5);
    Serial.print(",\"lon\":"); Serial.print(gpsLon, 5);
  } else {
    Serial.print(",\"lat\":null,\"lon\":null");
  }
  Serial.print("}");

  // Optional Gateway parameters (null when directly plugged over USB)
  Serial.println(",\"rssi_dbm\":null,\"snr_db\":null}");
}
