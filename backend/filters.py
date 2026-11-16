"""
MineSafe-AI Bridge Dashboard - Signal Processing, Filtering, and Spectral Analysis
"""

import numpy as np
from scipy.signal import butter, sosfilt, find_peaks
from typing import List, Tuple, Optional, Dict, Any


def design_highpass_filter(cutoff_hz: float = 0.5, fs: float = 500.0, order: int = 2):
    """
    Design a Butterworth high-pass filter (second-order sections) to remove gravity / DC offset.
    """
    nyquist = 0.5 * fs
    normal_cutoff = max(0.001, min(0.499, cutoff_hz / nyquist))
    sos = butter(order, normal_cutoff, btype='high', output='sos')
    return sos


def highpass_filter_series(signal: np.ndarray, sos: np.ndarray) -> np.ndarray:
    """
    Apply SOS filter along axis 0. If signal is too short, subtract mean as fallback.
    """
    if len(signal) < 8:
        return signal - np.mean(signal, axis=0) if len(signal) > 0 else signal
    try:
        filtered = sosfilt(sos, signal, axis=0)
        return filtered
    except Exception:
        return signal - np.mean(signal, axis=0)


def compute_vector_magnitude(ax: np.ndarray, ay: np.ndarray, az: np.ndarray) -> np.ndarray:
    """
    Compute vector magnitude sqrt(ax^2 + ay^2 + az^2).
    """
    return np.sqrt(ax**2 + ay**2 + az**2)


def compute_rms(values: np.ndarray) -> float:
    """
    Calculate Root Mean Square (RMS) of a signal array.
    """
    if len(values) == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(values))))


def compute_peak(values: np.ndarray) -> float:
    """
    Calculate maximum absolute peak of a signal array.
    """
    if len(values) == 0:
        return 0.0
    return float(np.max(np.abs(values)))


def compute_fft_spectrum(
    signal: np.ndarray,
    fs: float = 500.0,
    min_freq: float = 2.0,
    max_freq: float = 100.0,
    max_points: int = 150
) -> Tuple[Optional[float], List[float], List[float]]:
    """
    Compute FFT spectrum with Hanning window, and locate dominant natural frequency f0 peak
    within [min_freq, max_freq]. Returns (f0, freqs_list, amps_list).
    """
    n = len(signal)
    if n < int(fs * 0.5):  # Need at least 0.5s of data
        return None, [], []

    # Detrend and window
    detrended = signal - np.mean(signal)
    window = np.hanning(n)
    windowed = detrended * window

    # FFT
    fft_vals = np.fft.rfft(windowed)
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)
    amps = (2.0 / np.sum(window)) * np.abs(fft_vals)

    # Filter frequency range of interest (2 - 100 Hz)
    mask = (freqs >= min_freq) & (freqs <= max_freq)
    valid_freqs = freqs[mask]
    valid_amps = amps[mask]

    f0 = None
    if len(valid_amps) > 0:
        # Locate peaks
        peaks, properties = find_peaks(valid_amps, height=np.max(valid_amps) * 0.15)
        if len(peaks) > 0:
            best_peak_idx = peaks[np.argmax(valid_amps[peaks])]
            f0 = float(valid_freqs[best_peak_idx])
        else:
            best_idx = np.argmax(valid_amps)
            f0 = float(valid_freqs[best_idx])

    # Downsample spectrum for snappy UI transmission if too dense
    freq_indices = np.where((freqs >= 0.5) & (freqs <= max_freq))[0]
    sub_freqs = freqs[freq_indices]
    sub_amps = amps[freq_indices]

    if len(sub_freqs) > max_points:
        step = len(sub_freqs) // max_points
        sub_freqs = sub_freqs[::step]
        sub_amps = sub_amps[::step]

    freqs_out = [round(float(f), 2) for f in sub_freqs]
    amps_out = [round(float(a), 4) for a in sub_amps]

    return f0, freqs_out, amps_out


def compute_pitch_tilt(ax: float, ay: float, az: float) -> float:
    """
    Calculate pitch tilt angle in degrees:
    pitch = atan2(ax, sqrt(ay^2 + az^2)) * 180 / pi
    """
    denom = np.sqrt(ay**2 + az**2)
    if denom == 0.0 and ax == 0.0:
        return 0.0
    rad = np.arctan2(ax, denom)
    return float(np.degrees(rad))


def compute_batch_pitch(accel_samples: np.ndarray) -> float:
    """
    Compute average pitch tilt across a batch of [ax, ay, az] samples.
    """
    if len(accel_samples) == 0:
        return 0.0
    mean_axes = np.mean(accel_samples, axis=0)
    return compute_pitch_tilt(mean_axes[0], mean_axes[1], mean_axes[2])


def compute_flex_share_percent(flex_adc: int, flex_min: int, flex_max: int) -> float:
    """
    Normalize flex sensor ADC reading to percentage share of range (0.0 to 100.0%).
    """
    if flex_max <= flex_min:
        return 0.0
    clamped = max(flex_min, min(flex_max, flex_adc))
    return float(((clamped - flex_min) / (flex_max - flex_min)) * 100.0)


def compute_spatial_correlation(s1_sig: np.ndarray, s3_sig: np.ndarray) -> Tuple[Optional[float], Optional[float]]:
    """
    Compute amplitude ratio (RMS_S3 / RMS_S1) and normalized cross-correlation zero-lag peak.
    """
    if len(s1_sig) < 32 or len(s3_sig) < 32:
        return None, None

    rms1 = compute_rms(s1_sig)
    rms3 = compute_rms(s3_sig)

    ratio = (rms3 / rms1) if rms1 > 1e-4 else None

    # Normalized cross-correlation
    s1_norm = s1_sig - np.mean(s1_sig)
    s3_norm = s3_sig - np.mean(s3_sig)
    std1 = np.std(s1_sig)
    std3 = np.std(s3_sig)

    if std1 > 1e-4 and std3 > 1e-4:
        norm_factor = len(s1_norm) * std1 * std3
        xcorr = float(np.correlate(s1_norm, s3_norm, mode='valid')[0] / norm_factor)
        # clamp between -1.0 and 1.0
        xcorr = max(-1.0, min(1.0, xcorr))
    else:
        xcorr = None

    return ratio, xcorr
