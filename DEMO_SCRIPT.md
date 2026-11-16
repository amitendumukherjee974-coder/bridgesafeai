# MineSafe-AI Bridge Health Monitoring: 3-Minute Judges' Demo Script

**Project:** MineSafe-AI Bridge Health Monitoring (Scale Model Decision Support System)  
**Presenter:** Student Innovation Team  
**Audience:** Hackathon & Innovation Judges  
**Time Limit:** 3 Minutes  
**Equipment Ready on Bench:** Tabletop MDF bridge (60 &times; 12 cm), ESP32 sensor node connected via USB, 1 kg calibration weight, screwdriver, settlement jack on Pier 1, split joint with foil wire.

---

## 0:00 – 0:30 | The Hook & The "5-Second Judge Rule"

**Presenter Action:** Point to the physical scale bridge model and the dashboard projected on the screen.

**Presenter Speaks:**
> *"Judges, transportation infrastructure worldwide faces critical failure risks due to delayed manual inspections. We adapted the MineSafe-AI geotechnical sensing platform into an autonomous, real-time Structural Health Monitoring dashboard for road and rail bridges.*
> 
> *Our design strictly adheres to the **5-Second Rule**: within five seconds of walking up, a non-engineer judge can see:*
> 1. *The overarching bridge health status: **SAFE, WATCH, INSPECT, or UNSAFE**.*
> 2. *A plain-language diagnosis and actionable engineering recommendation.*
> 3. *Real multi-physics telemetry: 500 Hz vibration, tilt, laser deflection, flex, and pier load.*
> 
> *Most importantly: **REAL DATA ONLY**. There are zero random numbers, zero simulations, and zero fake demo modes. Every pixel on this screen is powered by packets from this ESP32 on the bridge."*

---

## 0:30 – 0:50 | Test 1 & 2: Integrity Check — Disconnect & Auto-Reconnect

**Presenter Action:** Unplug the USB cable from the laptop.

**Presenter Speaks:**
> *"To prove there is no fake data running in the background, watch what happens when I pull the USB cable..."*

- **On Screen:** Within 2 seconds, the banner snaps to grey: **"NO DATA – device disconnected"**. All rates drop to 0 pps, graph lines halt, sensor chips switch to offline.
- **Presenter Speaks:**
  > *"Notice the dashboard refuses to invent data. Now I plug the cable back in..."*
- **Presenter Action:** Plug the USB cable back in.
- **On Screen:** Within 1–2 seconds, the dashboard auto-detects the port, reconnects, the banner turns emerald green **SAFE**, and real telemetry resumes.

---

## 0:50 – 1:15 | Test 3: Structural Dynamics — Tap Impulse & FFT Modal Frequency

**Presenter Action:** Tap the bridge deck firmly near the mid-span with a screwdriver handle.

**Presenter Speaks:**
> *"Let's test dynamic modal vibration. I will introduce an impulse tap on the deck."*

- **On Screen:**
  - Within 200 ms, a sharp ring-down waveform appears on the 500 Hz vibration graph.
  - The FFT power spectrum computes the dominant peak, identifying the bridge's natural modal frequency **$f_0$ (e.g. ~24.5 Hz)** annotated directly against the baseline marker.
- **Presenter Speaks:**
  > *"Because our high-pass filter strips DC gravity in real time, the FFT isolates the true natural frequency $f_0$. If connections loosen, $f_0$ drops."*

---

## 1:15 – 1:50 | Test 4: Elastic Load vs. Permanent Structural Deformation

**Presenter Action:** Place the 1 kg metal weight onto the mid-span of the deck.

**Presenter Speaks:**
> *"Now let's apply a 1 kg live vehicle load directly over the span."*

- **On Screen:**
  - Load cell L1 rises to ~0.95 kg (sharing load across piers).
  - Laser Time-of-Flight D1 registers mid-span downward deflection (+1.4 mm).
  - Status banner remains **SAFE**: *"Normal live load response (0.95 kg active on span) — Elastic deflection observed."*
- **Presenter Speaks:**
  > *"The system understands structural engineering: temporary deflection under traffic is normal elastic response, NOT damage. Watch what happens when I remove the weight..."*
- **Presenter Action:** Remove the 1 kg weight.
- **On Screen:**
  - Deflection returns to within 0.1 mm of baseline.
  - The residual deformation remains under the 0.5 mm threshold, keeping the structure at SAFE.

---

## 1:50 – 2:20 | Test 5: Differential Pier Settlement (Settlement Jack)

**Presenter Action:** Rotate the screw jack under Pier 1 four full turns, lowering that pier by ~2 mm.

**Presenter Speaks:**
> *"A major cause of bridge failure is pier foundation subsidence. I will now turn the settlement jack on Pier 1."*

- **On Screen:**
  - Sensor S1 (deck mid-span) tilt steps to **+0.62°**, while Sensor S2 (ground base reference) remains steady at **0.0°**.
  - Status banner immediately turns **ORANGE (INSPECT)**.
  - Diagnosis: **"Local settlement near a pier (S1 tilt changed while ground S2 steady)"**.
  - Recommended Action: **"Inspect pier settlement jack and bearing; re-level pier foundation."**
- **Presenter Speaks:**
  > *"Because we compare deck sensor S1 against ground sensor S2, our decision engine knows it is local pier settlement, not a whole-room earthquake."*

---

## 2:20 – 2:45 | Test 6: Joint Damage & Crack Wire Tear

**Presenter Action:** Loosen the wing nut on the split joint splice plate and press down on that joint until the aluminium foil strip breaks.

**Presenter Speaks:**
> *"Now we simulate severe structural joint failure at our quarter-span splice joint."*

- **On Screen:**
  - Flex sensor F1 jumps from 15% to 58% of range.
  - Crack wire F2 flips from 0 to 1 (**"TORN (RED)"**).
  - Modal frequency $f_0$ drops by 12% due to stiffness loss.
  - Status banner flashes crimson **RED (UNSAFE)**.
  - Diagnosis: **"Joint or crack opening detected at the split joint"**.
  - Recommended Action: **"Inspect and tighten the splice plate; repair joint hardware."**

---

## 2:45 – 3:00 | Test 7 & Wrap-up: External Ground Shaking & Instant Report

**Presenter Action:** Vigorously shake the wooden ground base board.

**Presenter Speaks:**
> *"Finally, if an earthquake or heavy truck excites the foundation, we shake the base board..."*

- **On Screen:** Both S1 and S2 spike in vibration RMS, but S2 ground sensor spikes first. Diagnosis: *"External shaking (ground motion excitation): watch live vibration ring-down."*
- **Presenter Action:** Click the **"Save Report"** button on the top right.
- **On Screen:** A clean, 1-page printable inspection summary report opens immediately with timestamped events, baseline comparison tables, and peak values.
- **Presenter Conclusion:**
  > *"From zero-latency edge detection to actionable maintenance guidance, MineSafe-AI transforms passive structures into smart, self-reporting bridges — built entirely on real physics and real data. Thank you!"*
