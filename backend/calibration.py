"""
MineSafe-AI Bridge Dashboard - Baseline Calibration Engine
Accumulates real sensor telemetry for 30 seconds to establish empirical baseline statistics.
"""

import json
import os
import time
import numpy as np
from typing import Optional, Dict, Any, List
from datetime import datetime
from backend.models import BaselineProfile
from backend.filters import (
    compute_rms, compute_fft_spectrum, compute_pitch_tilt,
    design_highpass_filter, highpass_filter_series, compute_vector_magnitude
)


class CalibrationEngine:
    def __init__(self, config: Dict[str, Any], baseline_file_path: str = "baseline.json"):
        self.config = config
        self.baseline_file_path = baseline_file_path
        self.is_calibrating = False
        self.start_time = 0.0
        self.duration_sec = 30.0

        # Accumulated real data buffers
        self.raw_s1_samples: List[List[float]] = []
        self.raw_s2_samples: List[List[float]] = []
        self.raw_s3_samples: List[List[float]] = []
        self.summary_disp_samples: List[float] = []
        self.summary_flex_samples: List[int] = []

        # Current baseline profile
        self.profile = BaselineProfile()
        self.load_from_disk()

    def start_calibration(self, duration_sec: float = 30.0):
        """
        Begin a 30-second empirical calibration period.
        """
        self.duration_sec = duration_sec
        self.is_calibrating = True
        self.start_time = time.time()
        self.raw_s1_samples = []
        self.raw_s2_samples = []
        self.raw_s3_samples = []
        self.summary_disp_samples = []
        self.summary_flex_samples = []
        print(f"[Calibration] Started {duration_sec}s baseline calibration.")

    def get_progress(self) -> Dict[str, Any]:
        """
        Return calibration progress percentage and time remaining.
        """
        if not self.is_calibrating:
            return {"active": False, "progress_pct": 100.0 if self.profile.is_calibrated else 0.0, "remaining_sec": 0.0}
        elapsed = time.time() - self.start_time
        remaining = max(0.0, self.duration_sec - elapsed)
        pct = min(100.0, (elapsed / self.duration_sec) * 100.0)
        return {
            "active": True,
            "progress_pct": round(pct, 1),
            "remaining_sec": round(remaining, 1),
            "samples_collected": len(self.raw_s1_samples)
        }

    def feed_raw(self, s1_batch: List[List[float]], s2_batch: List[List[float]], s3_batch: List[List[float]]):
        """
        Feed real raw accelerometer samples during calibration.
        """
        if not self.is_calibrating:
            return
        if time.time() - self.start_time > self.duration_sec:
            self.finalize_calibration()
            return

        self.raw_s1_samples.extend(s1_batch)
        self.raw_s2_samples.extend(s2_batch)
        self.raw_s3_samples.extend(s3_batch)

    def feed_summary(self, disp_mm: float, flex_adc: int):
        """
        Feed real summary sensor samples during calibration.
        """
        if not self.is_calibrating:
            return
        if time.time() - self.start_time > self.duration_sec:
            self.finalize_calibration()
            return

        if disp_mm is not None and disp_mm > 0:
            self.summary_disp_samples.append(disp_mm)
        if flex_adc is not None and flex_adc > 0:
            self.summary_flex_samples.append(flex_adc)

    def finalize_calibration(self) -> BaselineProfile:
        """
        Compute mean and standard deviation from recorded real data and store baseline.
        """
        self.is_calibrating = False
        print(f"[Calibration] Finalizing with {len(self.raw_s1_samples)} raw samples...")

        if len(self.raw_s1_samples) < 100:
            print("[Calibration] Insufficient samples collected during calibration.")
            return self.profile

        fs = float(self.config.get("hardware", {}).get("sampling_rate_hz", 500.0))
        sos = design_highpass_filter(0.5, fs=fs)

        s1_arr = np.array(self.raw_s1_samples)
        s2_arr = np.array(self.raw_s2_samples)
        s3_arr = np.array(self.raw_s3_samples)

        # 1. Pitch tilt baseline (mean of static orientation)
        s1_pitch_mean = float(compute_pitch_tilt(np.mean(s1_arr[:, 0]), np.mean(s1_arr[:, 1]), np.mean(s1_arr[:, 2])))
        s2_pitch_mean = float(compute_pitch_tilt(np.mean(s2_arr[:, 0]), np.mean(s2_arr[:, 1]), np.mean(s2_arr[:, 2])))

        # 2. Vibration RMS baseline and standard deviation
        s1_filt = highpass_filter_series(s1_arr, sos)
        s2_filt = highpass_filter_series(s2_arr, sos)
        s3_filt = highpass_filter_series(s3_arr, sos)

        s1_mag = compute_vector_magnitude(s1_filt[:, 0], s1_filt[:, 1], s1_filt[:, 2])
        s2_mag = compute_vector_magnitude(s2_filt[:, 0], s2_filt[:, 1], s2_filt[:, 2])
        s3_mag = compute_vector_magnitude(s3_filt[:, 0], s3_filt[:, 1], s3_filt[:, 2])

        # Chunk into 1s windows to get RMS distribution (mean and sigma)
        chunk_size = int(fs)
        s1_rms_windows = [compute_rms(s1_mag[i:i+chunk_size]) for i in range(0, len(s1_mag) - chunk_size + 1, chunk_size)] or [compute_rms(s1_mag)]
        s2_rms_windows = [compute_rms(s2_mag[i:i+chunk_size]) for i in range(0, len(s2_mag) - chunk_size + 1, chunk_size)] or [compute_rms(s2_mag)]
        s3_rms_windows = [compute_rms(s3_mag[i:i+chunk_size]) for i in range(0, len(s3_mag) - chunk_size + 1, chunk_size)] or [compute_rms(s3_mag)]

        s1_rms_mean = float(np.mean(s1_rms_windows))
        s1_rms_std = float(np.std(s1_rms_windows)) if len(s1_rms_windows) > 1 else 0.005
        s1_rms_std = max(0.002, s1_rms_std)  # Floor to prevent division by zero

        s2_rms_mean = float(np.mean(s2_rms_windows))
        s2_rms_std = max(0.002, float(np.std(s2_rms_windows)) if len(s2_rms_windows) > 1 else 0.005)

        s3_rms_mean = float(np.mean(s3_rms_windows))
        s3_rms_std = max(0.002, float(np.std(s3_rms_windows)) if len(s3_rms_windows) > 1 else 0.005)

        # 3. Natural frequency f0
        f0, _, _ = compute_fft_spectrum(s1_mag, fs=fs, min_freq=2.0, max_freq=100.0)

        # 4. Displacement baseline
        disp_baseline = float(np.mean(self.summary_disp_samples)) if self.summary_disp_samples else None

        # 5. Flex sensor baseline
        flex_baseline = float(np.mean(self.summary_flex_samples)) if self.summary_flex_samples else None

        # 6. Spatial ratio S3/S1
        s3_s1_ratio = (s3_rms_mean / s1_rms_mean) if s1_rms_mean > 1e-4 else 1.0

        self.profile = BaselineProfile(
            is_calibrated=True,
            calibrated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            sample_count=len(self.raw_s1_samples),
            f0_hz=f0,
            s1_rms_mean=round(s1_rms_mean, 5),
            s1_rms_std=round(s1_rms_std, 5),
            s2_rms_mean=round(s2_rms_mean, 5),
            s2_rms_std=round(s2_rms_std, 5),
            s3_rms_mean=round(s3_rms_mean, 5),
            s3_rms_std=round(s3_rms_std, 5),
            s1_pitch_mean=round(s1_pitch_mean, 3),
            s2_pitch_mean=round(s2_pitch_mean, 3),
            disp_baseline_mm=round(disp_baseline, 2) if disp_baseline is not None else None,
            flex_adc_baseline=round(flex_baseline, 1) if flex_baseline is not None else None,
            s3_s1_ratio_mean=round(s3_s1_ratio, 3)
        )

        self.save_to_disk()
        print(f"[Calibration] Saved empirical baseline: f0={f0}Hz, disp={disp_baseline}mm, tilt={s1_pitch_mean}deg")
        return self.profile

    def save_to_disk(self):
        """
        Write calibrated baseline to JSON file on disk.
        """
        try:
            with open(self.baseline_file_path, "w") as f:
                json.dump(self.profile.model_dump(), f, indent=2)
        except Exception as e:
            print(f"[Calibration] Error saving baseline to disk: {e}")

    def load_from_disk(self) -> bool:
        """
        Load existing baseline from JSON file on disk if available.
        """
        if os.path.exists(self.baseline_file_path):
            try:
                with open(self.baseline_file_path, "r") as f:
                    data = json.load(f)
                    self.profile = BaselineProfile(**data)
                    print(f"[Calibration] Loaded baseline from {self.baseline_file_path} (Calibrated: {self.profile.calibrated_at})")
                    return True
            except Exception as e:
                print(f"[Calibration] Could not parse existing baseline: {e}")
        return False
