"""
MineSafe-AI Bridge Dashboard - Central Data Manager, Buffers, Recording & Replay Engine
Strictly processes real sensor data. Zero mock, synthetic, or generated data.
"""

import os
import time
import json
import threading
from collections import deque
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

import numpy as np
from backend.models import (
    RawPacket, SummaryPacket, SensorHealth, VibrationMetrics,
    TiltMetrics, DisplacementMetrics, CrackMetrics, SpatialMetrics,
    PowerMetrics, LinkMetrics, RiskEvaluation, BaselineProfile, EventLogEntry
)
from backend.filters import (
    design_highpass_filter, highpass_filter_series, compute_vector_magnitude,
    compute_rms, compute_peak, compute_fft_spectrum, compute_batch_pitch,
    compute_flex_share_percent, compute_spatial_correlation
)
from backend.risk_engine import RiskEngine
from backend.calibration import CalibrationEngine


class DataManager:
    def __init__(self, config: Dict[str, Any], base_dir: str = "."):
        self.config = config
        self.base_dir = base_dir
        self.recordings_dir = os.path.join(base_dir, "recordings")
        os.makedirs(self.recordings_dir, exist_ok=True)

        self.fs = float(config.get("hardware", {}).get("sampling_rate_hz", 500.0))
        self.disconnect_timeout = float(config.get("hardware", {}).get("disconnect_timeout_sec", 2.0))
        self.sos_filter = design_highpass_filter(0.5, fs=self.fs)

        # Baseline & Risk engines
        baseline_file = os.path.join(base_dir, "baseline.json")
        self.calibration_engine = CalibrationEngine(config, baseline_file_path=baseline_file)
        self.risk_engine = RiskEngine(config)

        # Sequence and loss tracking
        self.last_seq = -1
        self.total_packets_received = 0
        self.packets_lost = 0
        self.last_packet_time = 0.0
        self.pps_counter = 0
        self.last_pps_time = time.time()
        self.current_pps = 0.0

        # Sensor health states
        self.sensor_health = SensorHealth()

        # Circular sliding buffers (history for graphs)
        # Max capacity for 500 Hz vibration over 60 seconds = 30,000 samples
        self.max_wave_samples = int(self.fs * 60)
        self.wave_t_ms = deque(maxlen=self.max_wave_samples)
        self.wave_s1_mag = deque(maxlen=self.max_wave_samples)
        self.wave_s2_mag = deque(maxlen=self.max_wave_samples)
        self.wave_s3_mag = deque(maxlen=self.max_wave_samples)
        self.wave_s1_axes = deque(maxlen=self.max_wave_samples)  # [ax, ay, az]

        # Summary trend buffers (up to 3600 seconds = 1 hour at 1 Hz)
        self.max_summary_samples = 3600
        self.summary_times = deque(maxlen=self.max_summary_samples)
        self.trend_tilt_s1 = deque(maxlen=self.max_summary_samples)
        self.trend_tilt_s2 = deque(maxlen=self.max_summary_samples)
        self.trend_disp_mm = deque(maxlen=self.max_summary_samples)
        self.trend_flex_pct = deque(maxlen=self.max_summary_samples)
        self.trend_load_kg = deque(maxlen=self.max_summary_samples)
        self.trend_vbat_v = deque(maxlen=self.max_summary_samples)
        self.trend_vsol_v = deque(maxlen=self.max_summary_samples)
        self.trend_rms_s1 = deque(maxlen=self.max_summary_samples)
        self.trend_packet_loss = deque(maxlen=self.max_summary_samples)

        # Current live state variables (all initialized strictly to None or empty until real packet arrives)
        self.current_vibration = VibrationMetrics()
        self.current_tilt = TiltMetrics()
        self.current_disp = DisplacementMetrics()
        self.current_crack = CrackMetrics()
        self.current_spatial = SpatialMetrics()
        self.current_power = PowerMetrics()
        self.current_link = LinkMetrics()
        self.current_gps: Dict[str, Any] = {"fix": 0, "sats": 0, "lat": None, "lon": None}
        self.current_load_kg: Optional[float] = None
        self.current_shock_count: int = 0
        self.current_risk = RiskEvaluation()

        # FFT caching (recomputed every ~0.5s from the latest 2s window)
        self.last_fft_time = 0.0
        self.cached_f0: Optional[float] = None
        self.cached_spectrum_freqs: List[float] = []
        self.cached_spectrum_amps: List[float] = []

        # Event Log
        self.event_log: List[EventLogEntry] = []
        self.max_event_log = 200

        # Recording
        self.is_recording = False
        self.recording_file = None
        self.recording_filename = ""
        self.recording_start_time = 0.0
        self.recording_lock = threading.Lock()

        # Replay
        self.is_replaying = False
        self.replay_filename = ""
        self.replay_thread: Optional[threading.Thread] = None
        self.stop_replay_flag = False

        self.lock = threading.Lock()

    # --------------------------------------------------------------------------
    # Telemetry Ingestion (Raw & Summary Packets)
    # --------------------------------------------------------------------------
    def ingest_raw_packet(self, packet_dict: Dict[str, Any]):
        """
        Ingest a high-speed raw vibration packet (500 Hz batch).
        """
        now = time.time()
        with self.lock:
            self._update_link_stats(packet_dict.get("seq", -1), now)

            seq = packet_dict.get("seq", 0)
            t_ms = packet_dict.get("t_ms", 0)
            s1_batch = packet_dict.get("s1", [])
            s2_batch = packet_dict.get("s2", [])
            s3_batch = packet_dict.get("s3", [])

            # Update sensor presence
            self.sensor_health.s1_online = len(s1_batch) > 0
            self.sensor_health.s2_online = len(s2_batch) > 0
            self.sensor_health.s3_online = len(s3_batch) > 0

            # Feed calibration if in progress
            if self.calibration_engine.is_calibrating:
                self.calibration_engine.feed_raw(s1_batch, s2_batch, s3_batch)

            # Record if recording active
            if self.is_recording and self.recording_file:
                self._record_line(packet_dict)

            n_samples = len(s1_batch)
            if n_samples == 0:
                return

            dt_ms = 1000.0 / self.fs
            batch_times = [int(t_ms + i * dt_ms) for i in range(n_samples)]

            s1_arr = np.array(s1_batch)
            s2_arr = np.array(s2_batch) if len(s2_batch) > 0 else np.zeros((n_samples, 3))
            s3_arr = np.array(s3_batch) if len(s3_batch) > 0 else np.zeros((n_samples, 3))

            # Filter out DC/gravity component for vibration dynamics
            s1_filt = highpass_filter_series(s1_arr, self.sos_filter)
            s2_filt = highpass_filter_series(s2_arr, self.sos_filter)
            s3_filt = highpass_filter_series(s3_arr, self.sos_filter)

            # Magnitudes
            s1_mag = compute_vector_magnitude(s1_filt[:, 0], s1_filt[:, 1], s1_filt[:, 2])
            s2_mag = compute_vector_magnitude(s2_filt[:, 0], s2_filt[:, 1], s2_filt[:, 2])
            s3_mag = compute_vector_magnitude(s3_filt[:, 0], s3_filt[:, 1], s3_filt[:, 2])

            # Append to rolling buffers
            self.wave_t_ms.extend(batch_times)
            self.wave_s1_mag.extend(s1_mag.tolist())
            self.wave_s2_mag.extend(s2_mag.tolist())
            self.wave_s3_mag.extend(s3_mag.tolist())
            for row in s1_arr:
                self.wave_s1_axes.append(row.tolist())

            # Pitch tilt from low-pass/mean gravity orientation
            s1_pitch = compute_batch_pitch(s1_arr)
            s2_pitch = compute_batch_pitch(s2_arr)

            # Sliding 1s window RMS
            window_len = min(len(self.wave_s1_mag), int(self.fs))
            if window_len > 0:
                recent_s1 = list(self.wave_s1_mag)[-window_len:]
                recent_s2 = list(self.wave_s2_mag)[-window_len:]
                recent_s3 = list(self.wave_s3_mag)[-window_len:]
                s1_rms = compute_rms(np.array(recent_s1))
                s2_rms = compute_rms(np.array(recent_s2))
                s3_rms = compute_rms(np.array(recent_s3))
                s1_peak = compute_peak(np.array(recent_s1))
                s2_peak = compute_peak(np.array(recent_s2))
                s3_peak = compute_peak(np.array(recent_s3))
            else:
                s1_rms, s2_rms, s3_rms = 0.0, 0.0, 0.0
                s1_peak, s2_peak, s3_peak = 0.0, 0.0, 0.0

            # Periodic FFT calculation (~0.5s cadence, 2s window)
            if now - self.last_fft_time >= 0.5:
                fft_len = min(len(self.wave_s1_mag), int(self.fs * 2.0))
                if fft_len >= int(self.fs * 0.5):
                    fft_window_data = np.array(list(self.wave_s1_mag)[-fft_len:])
                    f0, freqs, amps = compute_fft_spectrum(
                        fft_window_data, fs=self.fs,
                        min_freq=float(self.config.get("filters", {}).get("fft_min_freq_hz", 2.0)),
                        max_freq=float(self.config.get("filters", {}).get("fft_max_freq_hz", 100.0))
                    )
                    self.cached_f0 = f0
                    self.cached_spectrum_freqs = freqs
                    self.cached_spectrum_amps = amps
                self.last_fft_time = now

            # Spatial correlation between S1 and S3
            if len(self.wave_s1_mag) >= int(self.fs):
                s1_sig = np.array(list(self.wave_s1_mag)[-int(self.fs):])
                s3_sig = np.array(list(self.wave_s3_mag)[-int(self.fs):])
                s3_s1_ratio, s3_s1_xcorr = compute_spatial_correlation(s1_sig, s3_sig)
                self.current_spatial = SpatialMetrics(s3_s1_ratio=s3_s1_ratio, s3_s1_cross_corr=s3_s1_xcorr)

            # Update vibration metrics
            base = self.calibration_engine.profile
            f0_drop_pct = None
            if self.cached_f0 is not None and base.is_calibrated and base.f0_hz:
                if self.cached_f0 < base.f0_hz:
                    f0_drop_pct = round(((base.f0_hz - self.cached_f0) / base.f0_hz) * 100.0, 1)

            self.current_vibration = VibrationMetrics(
                s1_rms_g=round(s1_rms, 4),
                s2_rms_g=round(s2_rms, 4),
                s3_rms_g=round(s3_rms, 4),
                s1_peak_g=round(s1_peak, 4),
                s2_peak_g=round(s2_peak, 4),
                s3_peak_g=round(s3_peak, 4),
                f0_hz=round(self.cached_f0, 2) if self.cached_f0 else None,
                f0_drop_pct=f0_drop_pct,
                spectrum_freqs=self.cached_spectrum_freqs,
                spectrum_amps=self.cached_spectrum_amps
            )

            # Update tilt metrics
            s1_delta = (s1_pitch - base.s1_pitch_mean) if (base.is_calibrated and base.s1_pitch_mean) else None
            s2_delta = (s2_pitch - base.s2_pitch_mean) if (base.is_calibrated and base.s2_pitch_mean) else None
            struct_settle = (s1_pitch - s2_pitch) if (s1_pitch is not None and s2_pitch is not None) else None

            self.current_tilt = TiltMetrics(
                s1_pitch_deg=round(s1_pitch, 2),
                s2_pitch_deg=round(s2_pitch, 2),
                s1_delta_deg=round(s1_delta, 2) if s1_delta is not None else None,
                s2_delta_deg=round(s2_delta, 2) if s2_delta is not None else None,
                structure_settlement_deg=round(struct_settle, 2) if struct_settle is not None else None,
                ground_tilt_deg=round(s2_pitch, 2)
            )

            # Evaluate risk logic
            self._run_risk_evaluation(now)

    def ingest_summary_packet(self, packet_dict: Dict[str, Any]):
        """
        Ingest a 1 Hz summary packet from slow sensors (laser ToF, flex, crack wire, load, battery, solar, GPS).
        """
        now = time.time()
        with self.lock:
            self._update_link_stats(packet_dict.get("seq", -1), now)

            disp_mm = packet_dict.get("disp_mm")
            flex_adc = packet_dict.get("flex_adc")
            crack_open = packet_dict.get("crack_open", 0)
            load_kg = packet_dict.get("load_kg")
            shock_count = packet_dict.get("shock_count", 0)
            vbat_v = packet_dict.get("vbat_v")
            vsol_v = packet_dict.get("vsol_v")
            gps_info = packet_dict.get("gps", {})
            rssi = packet_dict.get("rssi_dbm")
            snr = packet_dict.get("snr_db")

            # Update sensor online chips
            self.sensor_health.d1_online = (disp_mm is not None and disp_mm > 0)
            self.sensor_health.f1_online = (flex_adc is not None and flex_adc > 0)
            self.sensor_health.f2_online = (crack_open is not None)
            self.sensor_health.l1_online = (load_kg is not None)
            self.sensor_health.v1_online = (shock_count is not None)
            self.sensor_health.p1_online = (vbat_v is not None and vbat_v > 0.5)
            self.sensor_health.p2_online = (vsol_v is not None)
            self.sensor_health.r1_online = (rssi is not None)
            self.sensor_health.g1_online = (gps_info.get("fix", 0) == 1 or gps_info.get("sats", 0) > 0)

            # Feed calibration
            if self.calibration_engine.is_calibrating:
                self.calibration_engine.feed_summary(disp_mm, flex_adc)

            # Record if active
            if self.is_recording and self.recording_file:
                self._record_line(packet_dict)

            # Displacement
            base = self.calibration_engine.profile
            deflection = None
            if disp_mm is not None and base.is_calibrated and base.disp_baseline_mm:
                deflection = round(disp_mm - base.disp_baseline_mm, 2)

            self.current_disp = DisplacementMetrics(
                current_mm=round(disp_mm, 2) if disp_mm is not None else None,
                deflection_mm=deflection,
                residual_mm=round(self.risk_engine.unloaded_displacement_residual, 2),
                under_load=(load_kg is not None and load_kg > 0.25)
            )

            # Flex & Crack
            flex_min = self.config.get("calibration", {}).get("flex_adc_min", 1450)
            flex_max = self.config.get("calibration", {}).get("flex_adc_max", 3200)
            flex_pct = compute_flex_share_percent(flex_adc, flex_min, flex_max) if flex_adc is not None else None
            flex_delta = None
            if flex_pct is not None and base.is_calibrated and base.flex_adc_baseline:
                base_pct = compute_flex_share_percent(int(base.flex_adc_baseline), flex_min, flex_max)
                flex_delta = round(abs(flex_pct - base_pct), 1)

            self.current_crack = CrackMetrics(
                flex_adc=flex_adc,
                flex_percent=round(flex_pct, 1) if flex_pct is not None else None,
                flex_delta_percent=flex_delta,
                crack_open=crack_open
            )

            # Load & Shock
            self.current_load_kg = round(load_kg, 2) if load_kg is not None else None
            self.current_shock_count = shock_count

            # Power
            self.current_power = PowerMetrics(
                vbat_v=round(vbat_v, 2) if vbat_v is not None else None,
                vsol_v=round(vsol_v, 2) if vsol_v is not None else None,
                vbat_low=(vbat_v is not None and vbat_v < 3.4)
            )

            # GPS & RF
            self.current_gps = gps_info
            self.current_link.rssi_dbm = rssi
            self.current_link.snr_db = snr

            # Append to 1Hz summary trend buffers
            self.summary_times.append(datetime.now().strftime("%H:%M:%S"))
            if self.current_tilt.s1_pitch_deg is not None:
                self.trend_tilt_s1.append(self.current_tilt.s1_pitch_deg)
            if self.current_tilt.s2_pitch_deg is not None:
                self.trend_tilt_s2.append(self.current_tilt.s2_pitch_deg)
            if disp_mm is not None:
                self.trend_disp_mm.append(disp_mm)
            if flex_pct is not None:
                self.trend_flex_pct.append(flex_pct)
            if load_kg is not None:
                self.trend_load_kg.append(load_kg)
            if vbat_v is not None:
                self.trend_vbat_v.append(vbat_v)
            if vsol_v is not None:
                self.trend_vsol_v.append(vsol_v)
            if self.current_vibration.s1_rms_g is not None:
                self.trend_rms_s1.append(self.current_vibration.s1_rms_g)
            self.trend_packet_loss.append(self.current_link.packet_loss_pct)

            # Evaluate risk logic
            self._run_risk_evaluation(now)

    def _update_link_stats(self, seq: int, now: float):
        """
        Track packets per second, sequence gaps, and packet age.
        """
        self.last_packet_time = now
        self.total_packets_received += 1
        self.pps_counter += 1

        if now - self.last_pps_time >= 1.0:
            self.current_pps = self.pps_counter / (now - self.last_pps_time)
            self.pps_counter = 0
            self.last_pps_time = now

        if seq >= 0:
            if self.last_seq >= 0 and seq > self.last_seq + 1:
                dropped = seq - (self.last_seq + 1)
                self.packets_lost += dropped
            self.last_seq = seq

        loss_pct = 0.0
        total_expected = self.total_packets_received + self.packets_lost
        if total_expected > 0:
            loss_pct = round((self.packets_lost / total_expected) * 100.0, 1)

        self.current_link.pps = round(self.current_pps, 1)
        self.current_link.packets_lost = self.packets_lost
        self.current_link.packet_loss_pct = loss_pct
        self.current_link.last_packet_age_s = 0.0

    def _run_risk_evaluation(self, now: float):
        """
        Feed aggregated state into RiskEngine and log newly triggered events.
        """
        prev_level = self.current_risk.level

        metrics_dict = {
            "last_packet_age_s": now - self.last_packet_time if self.last_packet_time > 0 else 999.0,
            "s1_rms_g": self.current_vibration.s1_rms_g,
            "s2_rms_g": self.current_vibration.s2_rms_g,
            "s3_rms_g": self.current_vibration.s3_rms_g,
            "f0_hz": self.current_vibration.f0_hz,
            "s1_pitch_deg": self.current_tilt.s1_pitch_deg,
            "s2_pitch_deg": self.current_tilt.s2_pitch_deg,
            "disp_mm": self.current_disp.current_mm,
            "flex_adc": self.current_crack.flex_adc,
            "crack_open": self.current_crack.crack_open,
            "load_kg": self.current_load_kg,
            "vbat_v": self.current_power.vbat_v,
            "packet_loss_pct": self.current_link.packet_loss_pct
        }

        eval_result = self.risk_engine.evaluate(metrics_dict, self.calibration_engine.profile, now_ts=now)
        self.current_risk = eval_result

        # Log level transitions
        if eval_result.level != prev_level and eval_result.level in ["WATCH", "INSPECT", "UNSAFE"]:
            self.log_event(
                level=eval_result.level,
                source="RiskEngine",
                message=eval_result.reason,
                raw_value=eval_result.diagnosis
            )

    # --------------------------------------------------------------------------
    # Check Timeout (2-second silence detector)
    # --------------------------------------------------------------------------
    def check_connection_watchdog(self) -> RiskEvaluation:
        """
        Called by background loop or endpoint.
        If last packet > 2.0s ago, marks as NO DATA immediately.
        """
        with self.lock:
            now = time.time()
            age = (now - self.last_packet_time) if self.last_packet_time > 0 else 999.0
            self.current_link.last_packet_age_s = round(age, 1)

            if age > self.disconnect_timeout:
                self.current_pps = 0.0
                self.current_link.pps = 0.0
                # Mark offline chips
                self.sensor_health = SensorHealth()

                now_str = datetime.now().strftime("%H:%M:%S")
                self.current_risk = RiskEvaluation(
                    level="NO_DATA",
                    color="gray",
                    reason="NO DATA – device disconnected",
                    diagnosis="No telemetry received from bridge sensor node.",
                    recommended_action="Connect ESP32 sensor node via USB serial or Wi-Fi UDP.",
                    triggered_rules=["Hardware link inactive (> 2.0s silence)"],
                    timestamp=now_str,
                    is_temporary_load=False
                )
            return self.current_risk

    # --------------------------------------------------------------------------
    # Event Logging
    # --------------------------------------------------------------------------
    def log_event(self, level: str, source: str, message: str, raw_value: Optional[str] = None):
        """
        Append an entry to the event log.
        """
        entry = EventLogEntry(
            timestamp=datetime.now().strftime("%H:%M:%S"),
            level=level,
            source=source,
            message=message,
            raw_value=raw_value
        )
        self.event_log.insert(0, entry)
        if len(self.event_log) > self.max_event_log:
            self.event_log.pop()

        # If recording, write event note to recording stream
        if self.is_recording and self.recording_file:
            note_dict = {
                "type": "event_note",
                "t_host": datetime.now().isoformat(),
                "level": level,
                "source": source,
                "message": message,
                "raw_value": raw_value
            }
            self._record_line(note_dict)

    # --------------------------------------------------------------------------
    # Recording
    # --------------------------------------------------------------------------
    def start_recording(self) -> str:
        """
        Start recording real telemetry packets to a JSONL file.
        """
        with self.recording_lock:
            if self.is_recording:
                return self.recording_filename
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.recording_filename = f"session_{ts}.jsonl"
            filepath = os.path.join(self.recordings_dir, self.recording_filename)
            self.recording_file = open(filepath, "w", buffering=1, encoding="utf-8")
            self.is_recording = True
            self.recording_start_time = time.time()
            self.log_event("INFO", "Recorder", f"Started recording to {self.recording_filename}")
            return self.recording_filename

    def stop_recording(self) -> Optional[str]:
        """
        Stop active recording session and close file.
        """
        with self.recording_lock:
            if not self.is_recording:
                return None
            fname = self.recording_filename
            self.is_recording = False
            if self.recording_file:
                try:
                    self.recording_file.flush()
                    self.recording_file.close()
                except Exception:
                    pass
                self.recording_file = None
            self.log_event("INFO", "Recorder", f"Stopped recording {fname}")
            return fname

    def _record_line(self, data_dict: Dict[str, Any]):
        """
        Safely append a real packet JSON line to the active recording file.
        """
        try:
            line = json.dumps(data_dict) + "\n"
            self.recording_file.write(line)
        except Exception as e:
            print(f"[Recorder Error] Failed to write line: {e}")

    def list_recordings(self) -> List[Dict[str, Any]]:
        """
        List all available recorded real data sessions.
        """
        files = []
        if os.path.exists(self.recordings_dir):
            for f in sorted(os.listdir(self.recordings_dir), reverse=True):
                if f.endswith(".jsonl") or f.endswith(".csv"):
                    path = os.path.join(self.recordings_dir, f)
                    size_kb = round(os.path.getsize(path) / 1024.0, 1)
                    mtime = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M:%S")
                    files.append({"filename": f, "size_kb": size_kb, "date": mtime})
        return files

    # --------------------------------------------------------------------------
    # Replay (Recorded Real Data Only)
    # --------------------------------------------------------------------------
    def start_replay(self, filename: str) -> bool:
        """
        Start faithfully replaying a recorded real data file.
        Banner will prominently display: REPLAY of recorded real data (file name, date)
        """
        filepath = os.path.join(self.recordings_dir, filename)
        if not os.path.exists(filepath):
            return False

        self.stop_replay()
        self.is_replaying = True
        self.replay_filename = filename
        self.stop_replay_flag = False

        self.replay_thread = threading.Thread(target=self._replay_worker, args=(filepath,), daemon=True)
        self.replay_thread.start()
        self.log_event("INFO", "Replay", f"Started replay of {filename}")
        return True

    def stop_replay(self):
        """
        Stop active replay and restore live mode.
        """
        if self.is_replaying:
            self.stop_replay_flag = True
            if self.replay_thread and self.replay_thread.is_alive():
                self.replay_thread.join(timeout=1.0)
            self.is_replaying = False
            self.replay_filename = ""
            self.log_event("INFO", "Replay", "Replay stopped. Resuming live monitoring.")

    def _replay_worker(self, filepath: str):
        """
        Worker thread that reads recorded real packets and paces them faithfully.
        """
        print(f"[Replay] Starting replay from {filepath}")
        last_t_ms = None
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                if self.stop_replay_flag:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    packet = json.loads(line)
                    ptype = packet.get("type")
                    t_ms = packet.get("t_ms")

                    # Pace playback matching device millis
                    if last_t_ms is not None and t_ms is not None:
                        delta_ms = t_ms - last_t_ms
                        if 0 < delta_ms < 5000:
                            time.sleep(delta_ms / 1000.0)
                        else:
                            time.sleep(0.05)
                    else:
                        time.sleep(0.05)
                    last_t_ms = t_ms

                    if ptype == "raw":
                        self.ingest_raw_packet(packet)
                    elif ptype == "summary":
                        self.ingest_summary_packet(packet)
                except Exception as e:
                    print(f"[Replay error] Skipping line: {e}")

        self.is_replaying = False
        print(f"[Replay] Finished replaying {filepath}")

    # --------------------------------------------------------------------------
    # Telemetry Snapshot for UI WebSocket / Polling
    # --------------------------------------------------------------------------
    def get_ui_snapshot(self) -> Dict[str, Any]:
        """
        Return the consolidated dashboard state object for frontend rendering.
        """
        with self.lock:
            # Check connection timeout
            age = (time.time() - self.last_packet_time) if self.last_packet_time > 0 else 999.0
            if age > self.disconnect_timeout and not self.is_replaying:
                self.sensor_health = SensorHealth()
                now_str = datetime.now().strftime("%H:%M:%S")
                self.current_risk = RiskEvaluation(
                    level="NO_DATA",
                    color="gray",
                    reason="NO DATA – device disconnected",
                    diagnosis="No telemetry received from bridge sensor node.",
                    recommended_action="Connect ESP32 sensor node via USB serial or Wi-Fi UDP.",
                    triggered_rules=["Hardware link inactive (> 2.0s silence)"],
                    timestamp=now_str,
                    is_temporary_load=False
                )

            # Waveform snippet for live chart (last 250 points = 0.5s at 500Hz for high-speed streaming)
            wave_len = min(len(self.wave_s1_mag), 250)
            s1_mag_slice = list(self.wave_s1_mag)[-wave_len:] if wave_len > 0 else []
            s2_mag_slice = list(self.wave_s2_mag)[-wave_len:] if wave_len > 0 else []
            s3_mag_slice = list(self.wave_s3_mag)[-wave_len:] if wave_len > 0 else []
            t_ms_slice = list(self.wave_t_ms)[-wave_len:] if wave_len > 0 else []

            # RMS threshold lines from config and baseline
            base = self.calibration_engine.profile
            rms_thresholds = {"yellow": None, "orange": None, "red": None}
            if base.is_calibrated and base.s1_rms_mean is not None and base.s1_rms_std is not None:
                th_cfg = self.config.get("thresholds", {}).get("vibration_rms_sigma", {"yellow": 3, "orange": 6, "red": 10})
                rms_thresholds["yellow"] = round(base.s1_rms_mean + th_cfg.get("yellow", 3) * base.s1_rms_std, 4)
                rms_thresholds["orange"] = round(base.s1_rms_mean + th_cfg.get("orange", 6) * base.s1_rms_std, 4)
                rms_thresholds["red"] = round(base.s1_rms_mean + th_cfg.get("red", 10) * base.s1_rms_std, 4)

            return {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "is_replay": self.is_replaying,
                "replay_filename": self.replay_filename,
                "is_recording": self.is_recording,
                "recording_filename": self.recording_filename,
                "calibration": self.calibration_engine.get_progress(),
                "baseline": self.calibration_engine.profile.model_dump(),
                "risk": self.current_risk.model_dump(),
                "sensors_online": self.sensor_health.model_dump(),
                "link": self.current_link.model_dump(),
                "vibration": self.current_vibration.model_dump(),
                "rms_thresholds": rms_thresholds,
                "tilt": self.current_tilt.model_dump(),
                "displacement": self.current_disp.model_dump(),
                "crack": self.current_crack.model_dump(),
                "spatial": self.current_spatial.model_dump(),
                "load_kg": self.current_load_kg,
                "shock_count": self.current_shock_count,
                "power": self.current_power.model_dump(),
                "gps": self.current_gps,
                "waveform_latest": {
                    "t_ms": t_ms_slice,
                    "s1": s1_mag_slice,
                    "s2": s2_mag_slice,
                    "s3": s3_mag_slice
                },
                "event_log": [e.model_dump() for e in self.event_log[:20]]
            }

    # --------------------------------------------------------------------------
    # HTML / PDF Session Report Generator
    # --------------------------------------------------------------------------
    def generate_html_report(self) -> str:
        """
        Generate a comprehensive, single-page printable HTML session report
        containing status timeline, peak values, baseline comparisons, and events.
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        base = self.calibration_engine.profile

        event_rows = ""
        for ev in self.event_log[:50]:
            color = "#00e676" if ev.level == "INFO" else "#ffd600" if ev.level == "WATCH" else "#ff9100" if ev.level == "INSPECT" else "#ff1744"
            event_rows += f"""
            <tr>
                <td style="padding: 6px; border-bottom: 1px solid #333;">{ev.timestamp}</td>
                <td style="padding: 6px; border-bottom: 1px solid #333; color: {color}; font-weight: bold;">{ev.level}</td>
                <td style="padding: 6px; border-bottom: 1px solid #333;">{ev.source}</td>
                <td style="padding: 6px; border-bottom: 1px solid #333;">{ev.message}</td>
                <td style="padding: 6px; border-bottom: 1px solid #333; color: #888;">{ev.raw_value or ''}</td>
            </tr>
            """

        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>MineSafe-AI Bridge Health Monitoring - Session Report</title>
    <style>
        body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #0f141c; color: #e0e6ed; margin: 30px; }}
        h1, h2, h3 {{ color: #ffffff; }}
        .header {{ border-bottom: 2px solid #2d3748; padding-bottom: 15px; margin-bottom: 25px; }}
        .badge {{ display: inline-block; padding: 4px 10px; border-radius: 4px; font-weight: bold; font-size: 14px; }}
        .badge-safe {{ background: #00e676; color: #000; }}
        .badge-watch {{ background: #ffd600; color: #000; }}
        .badge-inspect {{ background: #ff9100; color: #000; }}
        .badge-unsafe {{ background: #ff1744; color: #fff; }}
        .badge-nodata {{ background: #78909c; color: #fff; }}
        .card {{ background: #1a2230; border: 1px solid #2d3748; border-radius: 6px; padding: 18px; margin-bottom: 20px; }}
        table {{ width: 100%; border-collapse: collapse; text-align: left; font-size: 13px; }}
        th {{ background: #252f3f; padding: 8px; border-bottom: 2px solid #4a5568; }}
        .stat-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin-bottom: 20px; }}
        .stat-box {{ background: #222b3c; padding: 12px; border-radius: 6px; }}
        .stat-label {{ font-size: 11px; text-transform: uppercase; color: #8fa0b5; }}
        .stat-val {{ font-size: 20px; font-weight: bold; margin-top: 5px; color: #fff; }}
        .footer {{ margin-top: 40px; font-size: 11px; color: #718096; border-top: 1px solid #2d3748; padding-top: 12px; text-align: center; }}
        @media print {{
            body {{ background: #fff; color: #000; }}
            .card, .stat-box {{ background: #f7fafc; border-color: #cbd5e0; }}
            th {{ background: #edf2f7; }}
            .stat-val {{ color: #000; }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>MineSafe-AI Bridge Health Monitoring - Session Report</h1>
        <div>Generated: <strong>{now_str}</strong> | Node ID: <strong>{self.config.get('hardware', {}).get('node_id', 'N1')}</strong></div>
    </div>

    <div class="card">
        <h2>Executive Status & Assessment</h2>
        <div style="font-size: 18px; margin: 10px 0;">
            Status: <span class="badge badge-{self.current_risk.level.lower()}">{self.current_risk.level}</span>
        </div>
        <p><strong>Primary Finding:</strong> {self.current_risk.reason}</p>
        <p><strong>Engineering Diagnosis:</strong> {self.current_risk.diagnosis}</p>
        <p><strong>Recommended Action:</strong> {self.current_risk.recommended_action}</p>
    </div>

    <div class="stat-grid">
        <div class="stat-box">
            <div class="stat-label">Deck S1 RMS Vibration</div>
            <div class="stat-val">{self.current_vibration.s1_rms_g or 0.0:.4f} g</div>
        </div>
        <div class="stat-box">
            <div class="stat-label">Natural Frequency (f0)</div>
            <div class="stat-val">{self.current_vibration.f0_hz or 'N/A'} Hz</div>
        </div>
        <div class="stat-box">
            <div class="stat-label">Structure S1 Tilt</div>
            <div class="stat-val">{self.current_tilt.s1_pitch_deg or 0.0:.2f}°</div>
        </div>
        <div class="stat-box">
            <div class="stat-label">Pier Deflection</div>
            <div class="stat-val">{self.current_disp.current_mm or 'N/A'} mm</div>
        </div>
        <div class="stat-box">
            <div class="stat-label">Flex Sensor Share</div>
            <div class="stat-val">{self.current_crack.flex_percent or 'N/A'}%</div>
        </div>
        <div class="stat-box">
            <div class="stat-label">Crack Foil Wire</div>
            <div class="stat-val">{'TORN (RED)' if self.current_crack.crack_open == 1 else 'INTACT (SAFE)'}</div>
        </div>
        <div class="stat-box">
            <div class="stat-label">Pier Load (estimated)</div>
            <div class="stat-val">{self.current_load_kg or 0.0:.2f} kg</div>
        </div>
        <div class="stat-box">
            <div class="stat-label">Node Battery</div>
            <div class="stat-val">{self.current_power.vbat_v or 'N/A'} V</div>
        </div>
    </div>

    <div class="card">
        <h2>Empirical Baseline Profile</h2>
        <p>Status: {'Calibrated (' + (base.calibrated_at or '') + ')' if base.is_calibrated else 'Not Calibrated'}</p>
        <table>
            <tr><th>Metric</th><th>Empirical Baseline Value</th><th>Current Value</th><th>Deviation</th></tr>
            <tr>
                <td>Modal Frequency f0</td>
                <td>{base.f0_hz or 'N/A'} Hz</td>
                <td>{self.current_vibration.f0_hz or 'N/A'} Hz</td>
                <td>{('-' + str(self.current_vibration.f0_drop_pct) + '%') if self.current_vibration.f0_drop_pct else '0.0%'}</td>
            </tr>
            <tr>
                <td>Deck Vibration RMS (mean ± σ)</td>
                <td>{base.s1_rms_mean or 'N/A'} ± {base.s1_rms_std or 'N/A'} g</td>
                <td>{self.current_vibration.s1_rms_g or 'N/A'} g</td>
                <td>-</td>
            </tr>
            <tr>
                <td>Deck Static Tilt</td>
                <td>{base.s1_pitch_mean or 'N/A'}°</td>
                <td>{self.current_tilt.s1_pitch_deg or 'N/A'}°</td>
                <td>{self.current_tilt.s1_delta_deg or 0.0:+.2f}°</td>
            </tr>
            <tr>
                <td>Displacement Distance</td>
                <td>{base.disp_baseline_mm or 'N/A'} mm</td>
                <td>{self.current_disp.current_mm or 'N/A'} mm</td>
                <td>{self.current_disp.deflection_mm or 0.0:+.2f} mm</td>
            </tr>
        </table>
    </div>

    <div class="card">
        <h2>Logged Chronological Events</h2>
        <table>
            <thead>
                <tr><th>Time</th><th>Severity</th><th>Source</th><th>Event Description</th><th>Raw Diagnostic</th></tr>
            </thead>
            <tbody>
                {event_rows or '<tr><td colspan="5" style="text-align:center; padding:10px; color:#888;">No threshold crossings or operator notes logged.</td></tr>'}
            </tbody>
        </table>
    </div>

    <div class="footer">
        Decision-support prototype on a scale model. Thresholds were tuned on the bench model. Not a safety certification.
        <br>MineSafe-AI Bridge Health Monitoring Platform &bull; Real Hardware Telemetry Only &bull; Offline Architecture
    </div>
</body>
</html>"""
        return html
