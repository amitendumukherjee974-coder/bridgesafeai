"""
MineSafe-AI Bridge Dashboard - Transparent Risk Engine and Diagnostic Decision Tree
"""

import time
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from backend.models import RiskEvaluation, BaselineProfile


class RiskEngine:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.thresholds = config.get("thresholds", {})
        self.hold_sec = float(config.get("filters", {}).get("status_hold_sec", 3.0))

        # Level hierarchy for worst-active calculation
        self.level_order = {"SAFE": 0, "WATCH": 1, "INSPECT": 2, "UNSAFE": 3}
        self.level_colors = {
            "SAFE": "green",
            "WATCH": "yellow",
            "INSPECT": "orange",
            "UNSAFE": "red",
            "NO_DATA": "gray"
        }

        # State holding / debouncing
        self.current_held_level = "SAFE"
        self.held_until_time = 0.0
        self.last_evaluation = None

        # Tracking load transitions (to distinguish live elastic deflection from permanent deformation)
        self.was_under_load = False
        self.unloaded_displacement_residual = 0.0

        # Optional Isolation Forest model
        self.iso_forest = None
        self.iso_forest_trained = False
        self.iso_forest_features = []

    def train_isolation_forest(self, real_baseline_feature_matrix: List[List[float]]):
        """
        Train Isolation Forest ONLY on recorded real baseline data.
        Never trained on synthetic or generated data.
        """
        if len(real_baseline_feature_matrix) < 50:
            return False
        try:
            from sklearn.ensemble import IsolationForest
            contamination = float(self.config.get("anomaly_detection", {}).get("contamination", 0.05))
            model = IsolationForest(contamination=contamination, random_state=42)
            model.fit(real_baseline_feature_matrix)
            self.iso_forest = model
            self.iso_forest_trained = True
            return True
        except Exception:
            return False

    def evaluate(
        self,
        current_metrics: Dict[str, Any],
        baseline: Optional[BaselineProfile],
        now_ts: Optional[float] = None
    ) -> RiskEvaluation:
        """
        Evaluate structural measurements against transparent engineering thresholds.
        """
        if now_ts is None:
            now_ts = time.time()

        now_str = datetime.now().strftime("%H:%M:%S")

        # 1. Check if device is connected
        last_packet_age = current_metrics.get("last_packet_age_s", 999.0)
        disconnect_timeout = float(self.config.get("hardware", {}).get("disconnect_timeout_sec", 2.0))
        if last_packet_age > disconnect_timeout:
            self.current_held_level = "NO_DATA"
            return RiskEvaluation(
                level="NO_DATA",
                color="gray",
                reason="NO DATA – device disconnected",
                diagnosis="No telemetry received from bridge sensor node.",
                recommended_action="Connect ESP32 sensor node via USB serial or Wi-Fi UDP.",
                triggered_rules=["Hardware link inactive (> 2.0s silence)"],
                timestamp=now_str,
                is_temporary_load=False
            )

        triggered_rules: List[Tuple[str, str]] = []  # (level, description)
        diagnosis = ""
        action = ""

        # Extract metrics
        s1_rms = current_metrics.get("s1_rms_g")
        s2_rms = current_metrics.get("s2_rms_g")
        s3_rms = current_metrics.get("s3_rms_g")
        f0_hz = current_metrics.get("f0_hz")
        s1_pitch = current_metrics.get("s1_pitch_deg")
        s2_pitch = current_metrics.get("s2_pitch_deg")
        disp_mm = current_metrics.get("disp_mm")
        flex_adc = current_metrics.get("flex_adc")
        crack_open = current_metrics.get("crack_open", 0)
        load_kg = current_metrics.get("load_kg", 0.0)
        vbat_v = current_metrics.get("vbat_v")
        packet_loss_pct = current_metrics.get("packet_loss_pct", 0.0)

        # Baseline reference checks
        has_baseline = baseline is not None and baseline.is_calibrated

        # Load transition tracking
        # Load cell threshold for tabletop bridge: > 0.25 kg indicates weight applied on deck
        is_currently_loaded = (load_kg is not None and load_kg > 0.25)
        if self.was_under_load and not is_currently_loaded:
            # Just unloaded! Calculate residual permanent displacement
            if has_baseline and baseline.disp_baseline_mm is not None and disp_mm is not None:
                self.unloaded_displacement_residual = abs(disp_mm - baseline.disp_baseline_mm)
            elif disp_mm is not None:
                self.unloaded_displacement_residual = 0.0
        self.was_under_load = is_currently_loaded

        # --- RULE 1: CRACK WIRE & FLEX SENSOR (Joint or Crack Opening) ---
        flex_delta_pct = 0.0
        if flex_adc is not None and self.config.get("calibration"):
            flex_min = self.config["calibration"].get("flex_adc_min", 1450)
            flex_max = self.config["calibration"].get("flex_adc_max", 3200)
            flex_span = max(1, flex_max - flex_min)
            current_flex_pct = max(0.0, min(100.0, ((flex_adc - flex_min) / flex_span) * 100.0))
            if has_baseline and baseline.flex_adc_baseline is not None:
                baseline_flex_pct = max(0.0, min(100.0, ((baseline.flex_adc_baseline - flex_min) / flex_span) * 100.0))
                flex_delta_pct = abs(current_flex_pct - baseline_flex_pct)
            else:
                flex_delta_pct = current_flex_pct

        flex_th = self.thresholds.get("flex_delta_percent", {"yellow": 20.0, "orange": 40.0, "red": 60.0})
        if crack_open == 1:
            triggered_rules.append(("UNSAFE", "Crack wire torn across split joint (F2 alarm)"))
        elif flex_delta_pct >= flex_th.get("red", 60.0):
            triggered_rules.append(("UNSAFE", f"Flex sensor deformation severe (+{flex_delta_pct:.1f}% range)"))
        elif flex_delta_pct >= flex_th.get("orange", 40.0):
            triggered_rules.append(("INSPECT", f"Flex sensor deformation elevated (+{flex_delta_pct:.1f}% range)"))
        elif flex_delta_pct >= flex_th.get("yellow", 20.0):
            triggered_rules.append(("WATCH", f"Flex sensor change noted (+{flex_delta_pct:.1f}% range)"))

        # --- RULE 2: TILT & SETTLEMENT ---
        s1_delta_tilt = 0.0
        s2_delta_tilt = 0.0
        if s1_pitch is not None and has_baseline and baseline.s1_pitch_mean is not None:
            s1_delta_tilt = abs(s1_pitch - baseline.s1_pitch_mean)
        if s2_pitch is not None and has_baseline and baseline.s2_pitch_mean is not None:
            s2_delta_tilt = abs(s2_pitch - baseline.s2_pitch_mean)

        tilt_th = self.thresholds.get("tilt_delta_deg", {"yellow": 0.3, "orange": 0.6, "red": 1.0})
        struct_tilt_change = s1_delta_tilt

        if struct_tilt_change >= tilt_th.get("red", 1.0):
            triggered_rules.append(("UNSAFE", f"Structure deck tilt change critical (+{struct_tilt_change:.2f}°)"))
        elif struct_tilt_change >= tilt_th.get("orange", 0.6):
            triggered_rules.append(("INSPECT", f"Structure deck tilt change significant (+{struct_tilt_change:.2f}°)"))
        elif struct_tilt_change >= tilt_th.get("yellow", 0.3):
            triggered_rules.append(("WATCH", f"Structure deck tilt change minor (+{struct_tilt_change:.2f}°)"))

        # --- RULE 3: DISPLACEMENT (Permanent vs Temporary Live Load) ---
        residual_th = self.thresholds.get("residual_disp_mm", {"yellow": 0.5, "orange": 1.0, "red": 2.0})
        if not is_currently_loaded and self.unloaded_displacement_residual > 0.0:
            res = self.unloaded_displacement_residual
            if res >= residual_th.get("red", 2.0):
                triggered_rules.append(("UNSAFE", f"Permanent residual deformation after unloading: {res:.2f} mm"))
            elif res >= residual_th.get("orange", 1.0):
                triggered_rules.append(("INSPECT", f"Permanent residual deformation after unloading: {res:.2f} mm"))
            elif res >= residual_th.get("yellow", 0.5):
                triggered_rules.append(("WATCH", f"Slight residual deformation after unloading: {res:.2f} mm"))

        # --- RULE 4: NATURAL FREQUENCY f0 DROP ---
        f0_drop_pct = 0.0
        if f0_hz is not None and has_baseline and baseline.f0_hz is not None and baseline.f0_hz > 0:
            if f0_hz < baseline.f0_hz:
                f0_drop_pct = ((baseline.f0_hz - f0_hz) / baseline.f0_hz) * 100.0

        f0_th = self.thresholds.get("f0_drop_percent", {"yellow": 5.0, "orange": 10.0, "red": 15.0})
        if f0_drop_pct >= f0_th.get("red", 15.0):
            triggered_rules.append(("UNSAFE", f"Modal natural frequency f0 drop severe (-{f0_drop_pct:.1f}%)"))
        elif f0_drop_pct >= f0_th.get("orange", 10.0):
            triggered_rules.append(("INSPECT", f"Modal natural frequency f0 drop significant (-{f0_drop_pct:.1f}%)"))
        elif f0_drop_pct >= f0_th.get("yellow", 5.0):
            triggered_rules.append(("WATCH", f"Modal natural frequency f0 drop detected (-{f0_drop_pct:.1f}%)"))

        # --- RULE 5: VIBRATION RMS ---
        rms_sigma_th = self.thresholds.get("vibration_rms_sigma", {"yellow": 3.0, "orange": 6.0, "red": 10.0})
        if s1_rms is not None and has_baseline and baseline.s1_rms_mean is not None and baseline.s1_rms_std is not None:
            sigma = max(1e-4, baseline.s1_rms_std)
            s1_sigma_diff = (s1_rms - baseline.s1_rms_mean) / sigma
            if s1_sigma_diff >= rms_sigma_th.get("red", 10.0):
                triggered_rules.append(("UNSAFE", f"Deck vibration RMS severe (+{s1_sigma_diff:.1f}σ above baseline)"))
            elif s1_sigma_diff >= rms_sigma_th.get("orange", 6.0):
                triggered_rules.append(("INSPECT", f"Deck vibration RMS high (+{s1_sigma_diff:.1f}σ above baseline)"))
            elif s1_sigma_diff >= rms_sigma_th.get("yellow", 3.0):
                triggered_rules.append(("WATCH", f"Deck vibration RMS elevated (+{s1_sigma_diff:.1f}σ above baseline)"))

        # --- RULE 6: POWER & LINK HEALTH (Node Health Only) ---
        bat_th = self.thresholds.get("battery_volts", {"yellow": 3.6, "orange": 3.4, "red": 3.3})
        if vbat_v is not None and vbat_v > 0.5:
            if vbat_v < bat_th.get("red", 3.3):
                triggered_rules.append(("INSPECT", f"Battery critically depleted ({vbat_v:.2f} V)"))
            elif vbat_v < bat_th.get("orange", 3.4):
                triggered_rules.append(("WATCH", f"Battery low ({vbat_v:.2f} V)"))
            elif vbat_v < bat_th.get("yellow", 3.6):
                triggered_rules.append(("WATCH", f"Battery discharging ({vbat_v:.2f} V)"))

        loss_th = self.thresholds.get("packet_loss_percent", {"yellow": 5.0, "orange": 15.0, "red": 30.0})
        if packet_loss_pct >= loss_th.get("red", 30.0):
            triggered_rules.append(("WATCH", f"Radio link packet loss high ({packet_loss_pct:.1f}%)"))

        # --- DIAGNOSTIC CLASSIFICATION (Table in Section 5) ---
        active_level = "SAFE"
        for lvl, _ in triggered_rules:
            if self.level_order.get(lvl, 0) > self.level_order.get(active_level, 0):
                active_level = lvl

        # Specific Diagnostic Pattern Matching
        if crack_open == 1 or flex_delta_pct >= flex_th.get("yellow", 20.0):
            diagnosis = "Joint or crack opening detected at the split joint"
            action = "Inspect and tighten the splice plate; repair joint hardware"
        elif not is_currently_loaded and self.unloaded_displacement_residual >= residual_th.get("yellow", 0.5):
            diagnosis = "Permanent deformation: deck did not return to baseline after unloading"
            action = "Inspect deck stiffness and bearings; repair or reduce permissible load"
        elif struct_tilt_change >= tilt_th.get("yellow", 0.3) and s2_delta_tilt < 0.2:
            diagnosis = "Local settlement near a pier (S1 tilt changed while ground S2 steady)"
            action = "Inspect pier settlement jack and bearing; re-level pier foundation"
        elif struct_tilt_change >= tilt_th.get("yellow", 0.3) and s2_delta_tilt >= tilt_th.get("yellow", 0.3):
            diagnosis = "Whole ground / base tilting (S1 deck and S2 base tilted together)"
            action = "Check ground foundation and base table stability"
        elif s2_rms is not None and s1_rms is not None and s2_rms > 0.05 and s2_rms >= s1_rms * 0.7:
            diagnosis = "External shaking (ground motion or heavy traffic excitation)"
            action = "Watch live vibration ring-down; inspect structure if shaking repeats"
        elif f0_drop_pct >= f0_th.get("yellow", 5.0):
            diagnosis = "Structural stiffness loss (natural frequency f0 dropped)"
            action = "Inspect structural cross-members and joint connections"
        elif vbat_v is not None and vbat_v < bat_th.get("orange", 3.4):
            diagnosis = "Node power supply problem (battery depleted / solar insufficient)"
            action = "Recharge 18650 cell or inspect solar panel alignment"
        elif packet_loss_pct >= loss_th.get("orange", 15.0):
            diagnosis = "Weak communication link (elevated packet drop)"
            action = "Reposition gateway antenna or verify LoRa line-of-sight"
        elif is_currently_loaded:
            diagnosis = f"Normal live load response ({load_kg:.2f} kg active load applied)"
            action = "Elastic deflection observed. Verify return to baseline upon unloading."
        else:
            diagnosis = "Deck vibration, tilt, deflection, and joint sensors nominal."
            action = "Normal operation; periodic monitoring active."

        # Debouncing / status hold
        # Hold the worst active level for hold_sec so single noise bursts don't flicker
        if self.level_order.get(active_level, 0) > self.level_order.get(self.current_held_level, 0):
            self.current_held_level = active_level
            self.held_until_time = now_ts + self.hold_sec
        elif now_ts > self.held_until_time:
            self.current_held_level = active_level

        # Synthesize primary one-sentence reason
        if self.current_held_level == "SAFE":
            if is_currently_loaded:
                reason = f"Normal live load response ({load_kg:.2f} kg active on span)"
            else:
                reason = "All structural parameters within calibrated baseline limits."
        else:
            reasons_for_held = [r for lvl, r in triggered_rules if lvl == self.current_held_level]
            if reasons_for_held:
                reason = reasons_for_held[0]
            elif triggered_rules:
                reason = triggered_rules[0][1]
            else:
                reason = f"Status held at {self.current_held_level} (settling)."

        color = self.level_colors.get(self.current_held_level, "green")
        rule_strings = [f"{lvl}: {desc}" for lvl, desc in triggered_rules]

        eval_result = RiskEvaluation(
            level=self.current_held_level,
            color=color,
            reason=reason,
            diagnosis=diagnosis,
            recommended_action=action,
            triggered_rules=rule_strings,
            timestamp=now_str,
            is_temporary_load=is_currently_loaded
        )
        self.last_evaluation = eval_result
        return eval_result
