/*
 ==============================================================================
  MineSafe-AI Bridge Health Monitoring - ESP32 LoRa Gateway Firmware (Optional)
  Receives 433 MHz LoRa packets from Bridge Sensor Node and forwards over USB
 ==============================================================================
  LoRa SX1278 Pins (SPI):
  - SCK:  GPIO 18
  - MISO: GPIO 19
  - MOSI: GPIO 23
  - NSS:  GPIO 5
  - RST:  GPIO 17
  - DIO0: GPIO 26
 ==============================================================================
*/

#include <SPI.h>
#include <LoRa.h>

#define LORA_SCK  18
#define LORA_MISO 19
#define LORA_MOSI 23
#define LORA_NSS  5
#define LORA_RST  17
#define LORA_DIO0 26

#define LORA_BAND 433E6
#define SERIAL_BAUD 115200

void setup() {
  Serial.begin(SERIAL_BAUD);
  while (!Serial && millis() < 2000);

  SPI.begin(LORA_SCK, LORA_MISO, LORA_MOSI, LORA_NSS);
  LoRa.setPins(LORA_NSS, LORA_RST, LORA_DIO0);

  if (!LoRa.begin(LORA_BAND)) {
    Serial.println("{\"type\":\"gateway_status\",\"status\":\"lora_init_failed\"}");
    while (1) delay(1000);
  }

  // High sensitivity sync configuration
  LoRa.setSyncWord(0x34);
  LoRa.setSpreadingFactor(7);
  LoRa.setSignalBandwidth(125E3);
  LoRa.setCodingRate4(5);

  Serial.println("{\"type\":\"gateway_status\",\"status\":\"lora_listening\",\"band\":433000000}");
}

void loop() {
  int packetSize = LoRa.parsePacket();
  if (packetSize) {
    String incoming = "";
    while (LoRa.available()) {
      incoming += (char)LoRa.read();
    }

    int rssi = LoRa.packetRssi();
    float snr = LoRa.packetSnr();

    // If summary packet, inject gateway RSSI and SNR
    if (incoming.indexOf("\"type\":\"summary\"") >= 0) {
      incoming.replace("\"rssi_dbm\":null", "\"rssi_dbm\":" + String(rssi));
      incoming.replace("\"snr_db\":null", "\"snr_db\":" + String(snr, 1));
    }

    Serial.println(incoming);
  }
}
