"""
MineSafe-AI Bridge Dashboard - Data Models and Schema Definitions
"""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime


class GPSData(BaseModel):
    fix: int = 0
    sats: int = 0
    lat: Optional[float] = None
    lon: Optional[float] = None


class RawPacket(BaseModel):
    type: str = "raw"
    node: str = "N1"
    seq: int
    t_ms: int
    fs: int = 500
    s1: List[List[float]] = []  # [[ax, ay, az], ...]
    s2: List[List[float]] = []
    s3: List[List[float]] = []


class SummaryPacket(BaseModel):
    type: str = "summary"
    node: str = "N1"
    seq: int
    t_ms: int
    disp_mm: float
    flex_adc: int
    crack_open: int  # 0 = intact, 1 = torn
    load_kg: float
    shock_count: int
    vbat_v: float
    vsol_v: float
    gps: GPSData = Field(default_factory=GPSData)
    rssi_dbm: Optional[float] = None
    snr_db: Optional[float] = None


class SensorHealth(BaseModel):
    s1_online: bool = False
    s2_online: bool = False
    s3_online: bool = False
    d1_online: bool = False
    f1_online: bool = False
    f2_online: bool = False
    l1_online: bool = False
    v1_online: bool = False
    p1_online: bool = False
    p2_online: bool = False
    r1_online: bool = False
    g1_online: bool = False


class VibrationMetrics(BaseModel):
    s1_rms_g: Optional[float] = None
    s2_rms_g: Optional[float] = None
    s3_rms_g: Optional[float] = None
    s1_peak_g: Optional[float] = None
    s2_peak_g: Optional[float] = None
    s3_peak_g: Optional[float] = None
    f0_hz: Optional[float] = None
    f0_drop_pct: Optional[float] = None
    spectrum_freqs: List[float] = []
    spectrum_amps: List[float] = []


class TiltMetrics(BaseModel):
    s1_pitch_deg: Optional[float] = None
    s2_pitch_deg: Optional[float] = None
    s1_delta_deg: Optional[float] = None
    s2_delta_deg: Optional[float] = None
    structure_settlement_deg: Optional[float] = None  # S1 - S2 (relative settlement)
    ground_tilt_deg: Optional[float] = None          # S2 alone (ground motion)


class DisplacementMetrics(BaseModel):
    current_mm: Optional[float] = None
    deflection_mm: Optional[float] = None            # current - baseline
    residual_mm: Optional[float] = None              # permanent deformation after unloading
    under_load: bool = False


class CrackMetrics(BaseModel):
    flex_adc: Optional[int] = None
    flex_percent: Optional[float] = None             # % of calibrated range
    flex_delta_percent: Optional[float] = None
    crack_open: Optional[int] = None                 # 0 = intact, 1 = torn


class SpatialMetrics(BaseModel):
    s3_s1_ratio: Optional[float] = None
    s3_s1_cross_corr: Optional[float] = None


class PowerMetrics(BaseModel):
    vbat_v: Optional[float] = None
    vsol_v: Optional[float] = None
    vbat_low: bool = False


class LinkMetrics(BaseModel):
    pps: float = 0.0
    packets_lost: int = 0
    packet_loss_pct: float = 0.0
    last_packet_age_s: float = 999.0
    rssi_dbm: Optional[float] = None
    snr_db: Optional[float] = None


class RiskEvaluation(BaseModel):
    level: str = "NO_DATA"  # SAFE, WATCH, INSPECT, UNSAFE, NO_DATA
    color: str = "gray"     # green, yellow, orange, red, gray
    reason: str = "NO DATA – device disconnected"
    diagnosis: str = "No telemetry received from bridge sensor node."
    recommended_action: str = "Connect ESP32 sensor node via USB serial or Wi-Fi UDP."
    triggered_rules: List[str] = []
    timestamp: str = ""
    is_temporary_load: bool = False


class BaselineProfile(BaseModel):
    is_calibrated: bool = False
    calibrated_at: Optional[str] = None
    sample_count: int = 0
    f0_hz: Optional[float] = None
    s1_rms_mean: Optional[float] = None
    s1_rms_std: Optional[float] = None
    s2_rms_mean: Optional[float] = None
    s2_rms_std: Optional[float] = None
    s3_rms_mean: Optional[float] = None
    s3_rms_std: Optional[float] = None
    s1_pitch_mean: Optional[float] = None
    s2_pitch_mean: Optional[float] = None
    disp_baseline_mm: Optional[float] = None
    flex_adc_baseline: Optional[float] = None
    s3_s1_ratio_mean: Optional[float] = None


class EventLogEntry(BaseModel):
    timestamp: str
    level: str  # INFO, WATCH, INSPECT, UNSAFE, NOTE
    source: str
    message: str
    raw_value: Optional[str] = None
