"""
MineSafe-AI Bridge Dashboard - Unit Tests for Parser, Filters & Risk Engine
Uses ONLY isolated hand-written protocol test fixtures strictly within tests/.
Never imported by or shown in the running application.
"""

import unittest
import numpy as np
import time

from backend.filters import (
    design_highpass_filter, highpass_filter_series, compute_vector_magnitude,
    compute_rms, compute_peak, compute_fft_spectrum, compute_pitch_tilt,
    compute_flex_share_percent, compute_spatial_correlation
)
from backend.risk_engine import RiskEngine
from backend.models import BaselineProfile, RiskEvaluation


class TestProtocolAndRisk(unittest.TestCase):

    def setUp(self):
        self.config = {
            "thresholds": {
                "vibration_rms_sigma": {"yellow": 3.0, "orange": 6.0, "red": 10.0},
                "tilt_delta_deg": {"yellow": 0.3, "orange": 0.6, "red": 1.0},
                "residual_disp_mm": {"yellow": 0.5, "orange": 1.0, "red": 2.0},
                "flex_delta_percent": {"yellow": 20.0, "orange": 40.0, "red": 60.0},
                "f0_drop_percent": {"yellow": 5.0, "orange": 10.0, "red": 15.0},
                "battery_volts": {"yellow": 3.6, "orange": 3.4, "red": 3.3},
                "packet_loss_percent": {"yellow": 5.0, "orange": 15.0, "red": 30.0}
            },
            "calibration": {
                "flex_adc_min": 1450,
                "flex_adc_max": 3200,
                "hx711_scale": 420.0
            },
            "filters": {
                "status_hold_sec": 0.1  # Low hold time for unit testing
            },
            "hardware": {
                "disconnect_timeout_sec": 2.0
            }
        }
        self.risk_engine = RiskEngine(self.config)

    # --------------------------------------------------------------------------
    # 1. Filter and Math Unit Tests
    # --------------------------------------------------------------------------
    def test_pitch_tilt_calculation(self):
        # Stationary flat: ax = 0, ay = 0, az = 1g -> pitch = 0 deg
        self.assertAlmostEqual(compute_pitch_tilt(0.0, 0.0, 1.0), 0.0, places=2)

        # 30 deg pitch upward: ax = sin(30) = 0.5, az = cos(30) = 0.866
        self.assertAlmostEqual(compute_pitch_tilt(0.5, 0.0, 0.866), 30.0, places=1)

        # 90 deg vertical
        self.assertAlmostEqual(compute_pitch_tilt(1.0, 0.0, 0.0), 90.0, places=1)

    def test_flex_normalization(self):
        # 1450 min -> 0%
        self.assertEqual(compute_flex_share_percent(1450, 1450, 3200), 0.0)
        # 3200 max -> 100%
        self.assertEqual(compute_flex_share_percent(3200, 1450, 3200), 100.0)
        # Midpoint 2325 -> 50%
        self.assertAlmostEqual(compute_flex_share_percent(2325, 1450, 3200), 50.0, places=1)
        # Clamping out-of-range
        self.assertEqual(compute_flex_share_percent(1000, 1450, 3200), 0.0)
        self.assertEqual(compute_flex_share_percent(4000, 1450, 3200), 100.0)

    def test_rms_and_vector_magnitude(self):
        ax = np.array([1.0, 0.0])
        ay = np.array([0.0, 2.0])
        az = np.array([0.0, 0.0])
        mag = compute_vector_magnitude(ax, ay, az)
        self.assertEqual(len(mag), 2)
        self.assertAlmostEqual(mag[0], 1.0)
        self.assertAlmostEqual(mag[1], 2.0)

        # RMS of [3, 4] -> sqrt((9 + 16)/2) = sqrt(12.5) ~ 3.535
        self.assertAlmostEqual(compute_rms(np.array([3.0, 4.0])), np.sqrt(12.5), places=3)
        self.assertEqual(compute_peak(np.array([-5.2, 3.1])), 5.2)

    def test_highpass_filter_dc_removal(self):
        fs = 500.0
        sos = design_highpass_filter(0.5, fs=fs)
        # Signal with DC bias of 1.0g (gravity) over 4 seconds so 0.5 Hz filter settles
        t = np.linspace(0, 4.0, int(fs * 4.0))
        pure_dc = np.ones((len(t), 3))
        filtered = highpass_filter_series(pure_dc, sos)
        # After settling (after 2s = sample 1000), high-passed DC should be near 0
        self.assertAlmostEqual(float(np.mean(filtered[1000:, 0])), 0.0, places=2)

    def test_fft_peak_identification(self):
        fs = 500.0
        t = np.linspace(0, 2.0, int(fs * 2.0), endpoint=False)
        target_f0 = 24.5  # 24.5 Hz bridge modal resonance
        test_tone = np.sin(2 * np.pi * target_f0 * t)

        f0, freqs, amps = compute_fft_spectrum(test_tone, fs=fs, min_freq=2.0, max_freq=100.0)
        self.assertIsNotNone(f0)
        self.assertAlmostEqual(f0, target_f0, delta=0.5)

    def test_spatial_correlation(self):
        t = np.linspace(0, 1.0, 500)
        s1 = np.sin(2 * np.pi * 10.0 * t)
        s3 = 0.5 * s1  # Half amplitude
        ratio, xcorr = compute_spatial_correlation(s1, s3)
        self.assertIsNotNone(ratio)
        self.assertAlmostEqual(ratio, 0.5, places=2)
        self.assertAlmostEqual(xcorr, 1.0, places=2)

    # --------------------------------------------------------------------------
    # 2. Risk Engine Transparent Decision Tree Tests
    # --------------------------------------------------------------------------
    def test_disconnected_timeout_produces_no_data(self):
        metrics = {"last_packet_age_s": 2.5}  # Exceeds 2.0s timeout
        res = self.risk_engine.evaluate(metrics, None)
        self.assertEqual(res.level, "NO_DATA")
        self.assertEqual(res.color, "gray")
        self.assertIn("NO DATA", res.reason)

    def test_safe_baseline_state(self):
        baseline = BaselineProfile(
            is_calibrated=True,
            f0_hz=25.0,
            s1_rms_mean=0.010,
            s1_rms_std=0.002,
            s1_pitch_mean=0.0,
            s2_pitch_mean=0.0,
            disp_baseline_mm=85.0,
            flex_adc_baseline=1500
        )
        metrics = {
            "last_packet_age_s": 0.05,
            "s1_rms_g": 0.011,
            "s1_pitch_deg": 0.05,
            "s2_pitch_deg": 0.02,
            "f0_hz": 25.0,
            "disp_mm": 85.0,
            "flex_adc": 1500,
            "crack_open": 0,
            "load_kg": 0.0,
            "vbat_v": 4.1
        }
        res = self.risk_engine.evaluate(metrics, baseline)
        self.assertEqual(res.level, "SAFE")
        self.assertEqual(res.color, "green")
        self.assertIn("nominal", res.diagnosis.lower())

    def test_local_settlement_detection(self):
        baseline = BaselineProfile(
            is_calibrated=True,
            s1_pitch_mean=0.0,
            s2_pitch_mean=0.0
        )
        # Pier settlement jack turned: S1 steps by +0.65 deg, ground S2 remains at 0.05 deg
        metrics = {
            "last_packet_age_s": 0.05,
            "s1_pitch_deg": 0.65,
            "s2_pitch_deg": 0.05,
            "disp_mm": 85.0,
            "flex_adc": 1500,
            "crack_open": 0,
            "load_kg": 0.0,
            "vbat_v": 4.0
        }
        res = self.risk_engine.evaluate(metrics, baseline)
        self.assertEqual(res.level, "INSPECT")
        self.assertEqual(res.color, "orange")
        self.assertIn("Local settlement near a pier", res.diagnosis)
        self.assertIn("Inspect pier settlement jack", res.recommended_action)

    def test_whole_ground_tilt_detection(self):
        baseline = BaselineProfile(
            is_calibrated=True,
            s1_pitch_mean=0.0,
            s2_pitch_mean=0.0
        )
        # Base table tilted: Both S1 and S2 tilt by 0.60 deg
        metrics = {
            "last_packet_age_s": 0.05,
            "s1_pitch_deg": 0.60,
            "s2_pitch_deg": 0.60,
            "disp_mm": 85.0,
            "flex_adc": 1500,
            "crack_open": 0,
            "load_kg": 0.0,
            "vbat_v": 4.0
        }
        res = self.risk_engine.evaluate(metrics, baseline)
        self.assertEqual(res.level, "INSPECT")
        self.assertIn("Whole ground / base tilting", res.diagnosis)

    def test_crack_wire_torn_triggers_unsafe(self):
        metrics = {
            "last_packet_age_s": 0.05,
            "crack_open": 1,  # Foil wire torn
            "vbat_v": 4.0
        }
        res = self.risk_engine.evaluate(metrics, None)
        self.assertEqual(res.level, "UNSAFE")
        self.assertEqual(res.color, "red")
        self.assertIn("Joint or crack opening", res.diagnosis)
        self.assertIn("tighten the splice plate", res.recommended_action)

    def test_temporary_load_vs_permanent_deformation(self):
        baseline = BaselineProfile(
            is_calibrated=True,
            disp_baseline_mm=85.0
        )
        # 1. 1 kg placed on deck -> load rises to 0.95 kg, disp deflects to 87.2 mm
        metrics_loaded = {
            "last_packet_age_s": 0.05,
            "load_kg": 0.95,
            "disp_mm": 87.2,
            "vbat_v": 4.0
        }
        res1 = self.risk_engine.evaluate(metrics_loaded, baseline)
        self.assertEqual(res1.level, "SAFE")
        self.assertTrue(res1.is_temporary_load)
        self.assertIn("Normal live load response", res1.diagnosis)

        # 2. Load removed and deck returns to 85.1 mm (residual 0.1 mm < 0.5 mm threshold)
        metrics_unloaded_normal = {
            "last_packet_age_s": 0.05,
            "load_kg": 0.02,
            "disp_mm": 85.1,
            "vbat_v": 4.0
        }
        res2 = self.risk_engine.evaluate(metrics_unloaded_normal, baseline, now_ts=time.time() + 1.0)
        self.assertEqual(res2.level, "SAFE")
        self.assertAlmostEqual(self.risk_engine.unloaded_displacement_residual, 0.1, places=2)

        # 3. Permanent deformation scenario: Load removed but deck stays bent at 86.8 mm (residual 1.8 mm)
        self.risk_engine.was_under_load = True
        metrics_unloaded_damaged = {
            "last_packet_age_s": 0.05,
            "load_kg": 0.01,
            "disp_mm": 86.8,
            "vbat_v": 4.0
        }
        res3 = self.risk_engine.evaluate(metrics_unloaded_damaged, baseline, now_ts=time.time() + 2.0)
        self.assertEqual(res3.level, "INSPECT")
        self.assertIn("Permanent deformation", res3.diagnosis)


if __name__ == "__main__":
    unittest.main()
