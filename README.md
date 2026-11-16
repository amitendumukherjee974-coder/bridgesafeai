# MineSafe-AI Bridge Health Monitoring Dashboard

A real-time, offline Structural Health Monitoring (SHM) dashboard designed for **road and rail bridges**, showcased on a physical 60 &times; 12 cm tabletop scale bridge model fitted with an ESP32 multi-sensor node.

Built for **innovation competitions and hackathon judges**, the dashboard provides instant situational awareness within 5 seconds of glance, diagnosing structural degradation and providing plain-language engineering recommendations.

---

## Key Features

1. **The 5-Second Judge View**:
   - Prominent Status Banner: **SAFE (Green)**, **WATCH (Yellow)**, **INSPECT (Orange)**, or **UNSAFE (Red)** with a single-sentence reason and recommended repair action.
   - Clear distinction between **temporary live elastic deflection** (e.g. 1 kg vehicle load crossing span) and **permanent structural damage** (residual deflection remaining after unloading).
2. **Strict Non-Negotiable Rule: REAL DATA ONLY**:
   - Zero simulated, mocked, random, or placeholder numbers in the running application.
   - Disconnecting the hardware turns the banner grey (**"NO DATA – device disconnected"**) within 2.0 seconds and stops graph advancement.
   - Replay mode strictly replays files recorded from real hardware, labelled clearly with `REPLAY of recorded real data (file name, date)`.
3. **Multi-Physics Sensor Processing (Edge & Backend)**:
   - **500 Hz Vibration Dynamics**: High-pass Butterworth filter (0.5 Hz) removing DC gravity, vector magnitude calculation, and sliding 1.0s window RMS & peak tracking.
   - **Modal Analysis**: Fast Fourier Transform (FFT) over 2.0s window identifying fundamental natural resonance frequency ($f_0$) between 2 and 100 Hz.
   - **Differential Settlement**: Structure deck pitch tilt (S1) vs. ground reference pitch tilt (S2).
   - **Laser Deflection**: VL53L0X Time-of-Flight laser measuring mid-span distance and residual deformation.
   - **Joint Integrity**: Flex bend sensor and binary aluminium foil crack wire ($0 = \text{intact}, 1 = \text{torn}$).
   - **Pier Load**: Calibrated HX711 1 kg load cell under pier.
   - **Telemetry & Power**: 18650 battery voltage, 6V solar panel harvesting, SX1278 LoRa packet loss/RSSI, and NEO-6M GPS.
4. **Interactive Scale Bridge Map**:
   - Side-view SVG schematic of the tabletop bridge showing Pier 1 (settlement jack), Pier 2, split joint, and sensor pins glowing by real-time status. Clicking any sensor scrolls to its detailed telemetry tile.
5. **Session Recording, Replay & 1-Click Reporting**:
   - Records incoming packets to timestamped `.jsonl` files.
   - Instant 1-page printable HTML inspection summary report with event chronology and baseline comparison tables.

---

## Hardware Pinout Reference (ESP32 DevKit 38-Pin)

| Sensor ID | Sensor Part | Bus / Pin | Measures | Functional Role |
|---|---|---|---|---|
| **S1** | MPU6050 (0x68) | I2C (SDA 21, SCL 22, AD0 &rarr; GND) | 3-axis accel, pitch tilt | Mid-span deck vibration & tilt |
| **S2** | MPU6050 (0x69) | I2C (SDA 21, SCL 22, AD0 &rarr; 3.3V) | 3-axis accel, pitch tilt | Base board ground motion reference |
| **S3** | ADXL345 (0x53) | I2C (SDA 21, SCL 22, SDO &rarr; GND) | 3-axis acceleration | Quarter-span deck spatial correlation |
| **D1** | VL53L0X | I2C (0x29) | Distance to deck (mm) | Time-of-Flight laser deflection |
| **F1** | Flex Sensor | ADC1 GPIO 35 | Resistance bend (ADC counts) | Split joint flexure / opening |
| **F2** | Crack Wire | GPIO 25 (`INPUT_PULLUP`) | 0 = Intact, 1 = Torn | Instant binary crack alarm |
| **L1** | HX711 + Load Cell | DT = GPIO 16, SCK = GPIO 4 | Weight (kg) | Calibrated pier applied load |
| **V1** | SW-420 | GPIO 34 (Interrupt) | Pulse counts | Wake-up / shock trigger |
| **P1** | Battery Divider | ADC1 GPIO 39 (100k + 100k) | Volts (0 - 4.2V) | 18650 Li-ion cell health |
| **P2** | Solar Divider | ADC1 GPIO 36 (100k + 47k) | Volts (0 - 6.0V) | 6V solar panel harvesting |
| **G1** | NEO-6M GPS | Serial2 RX = GPIO 27 | Fix, Sats, Lat, Lon | Location fix (reports "no fix" indoors) |
| **R1** | LoRa SX1278 | SCK 18, MISO 19, MOSI 23, NSS 5, RST 17, DIO0 26 | 433 MHz RF packets | Long-range wireless telemetry link |
| **LED** | 3x LEDs | Green = 32, Yellow = 33, Red = 14 | Digital Output | Local visual status indicators |
| **BUZ** | Active Buzzer | GPIO 13 | Digital Output | Audible alarm on joint tear |

---

## Data Protocol Contract

The ESP32 firmware emits newline-delimited JSON over USB serial (`115200` or `921600` baud) and optionally over Wi-Fi UDP (port `5005`):

### 1. High-Speed Raw Packet (~20 per second, 25 samples @ 500 Hz):
```json
{
  "type": "raw",
  "node": "N1",
  "seq": 1420,
  "t_ms": 78200,
  "fs": 500,
  "s1": [[0.012, -0.005, 0.998], [0.015, -0.004, 1.002], "..."],
  "s2": [[-0.001, 0.002, 1.000], "..."],
  "s3": [[0.008, -0.002, 0.995], "..."]
}
```

### 2. Summary Packet (1 Hz, slow sensors):
```json
{
  "type": "summary",
  "node": "N1",
  "seq": 1421,
  "t_ms": 78500,
  "disp_mm": 85.2,
  "flex_adc": 1520,
  "crack_open": 0,
  "load_kg": 0.00,
  "shock_count": 0,
  "vbat_v": 4.05,
  "vsol_v": 5.40,
  "gps": {"fix": 0, "sats": 0, "lat": null, "lon": null},
  "rssi_dbm": null,
  "snr_db": null
}
```

---

## Installation & Running

### Requirements
- Python 3.10+
- Installed packages: `fastapi`, `uvicorn`, `pyserial`, `pyyaml`, `numpy`, `scipy`, `pandas`, `scikit-learn`

### Quick Start
1. Navigate to the project directory:
   ```bash
   cd minesafe_bridge_dashboard
   ```
2. Start the FastAPI server:
   ```bash
   python -m uvicorn backend.app:app --host 0.0.0.0 --port 8000
   ```
3. Open your browser at:
   ```
   http://localhost:8000
   ```
   The dashboard will automatically scan for the connected ESP32 on USB COM ports.

---

## 30-Second Baseline Calibration

Before running demonstrations:
1. Ensure the tabletop bridge model is placed on a stable surface without external loads.
2. Click the **"Calibrate (30s)"** button on the bottom control panel.
3. Keep hands off the bridge for 30 seconds.
4. The engine records empirical mean and standard deviation ($\mu, \sigma$) for vibration, baseline modal frequency $f_0$, baseline static pitch tilt, laser distance, and flex resistance.
5. Baseline statistics are stored locally in `baseline.json`.

---

## Verification & Unit Tests

Run the complete test suite verifying protocol parsing, DC gravity filtering, RMS calculations, FFT peak detection, and the diagnostic decision tree:
```bash
python -m unittest tests.test_protocol_and_risk
```

---

## Decision Support Disclaimer

*This application is a scale model decision-support prototype. Thresholds were tuned empirically on the bench model. It provides early detection, anomaly detection, and risk estimation to assist structural engineers in scheduling maintenance inspections; it does not certify structural safety.*
"# bridgesafeai" 
