from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

RUNNING_WINDOW = 31
EPOCH_SECONDS = 30
SEVERITY_BINS = np.array([0.5, 3.0, 5.0], dtype=float)
SEVERITY_LABELS = np.array(["S0", "S1", "S2", "S3"])
PROMOTION_THRESHOLD_S = 120.0
BLUE_THRESHOLD = 0.1


@dataclass
class Episode:
    system: str
    prn: str
    start_utc: pd.Timestamp
    end_utc: pd.Timestamp
    duration_s: float
    peak_ns: float
    mean_ns: float
    z_max: float
    sigma_ns: float
    epoch_seconds: int
    severity: str
    episode_date: pd.Timestamp
    epoch_count: int
    max_epoch_severity: str
    promoted: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "system": self.system,
            "prn": self.prn,
            "start_utc": self.start_utc,
            "end_utc": self.end_utc,
            "duration_s": self.duration_s,
            "peak_ns": self.peak_ns,
            "mean_ns": self.mean_ns,
            "z_max": self.z_max,
            "sigma_ns": self.sigma_ns,
            "epoch_seconds": self.epoch_seconds,
            "severity": self.severity,
            "episode_date": self.episode_date,
            "epoch_count": self.epoch_count,
            "max_epoch_severity": self.max_epoch_severity,
            "promoted": self.promoted,
        }


def _running_median(series: pd.Series) -> pd.Series:
    return series.rolling(window=RUNNING_WINDOW, center=True, min_periods=1).median()


def _median_absolute_deviation(series: pd.Series) -> float:
    median = series.median()
    mad = (series - median).abs().median()
    return float(mad)


def _severity_codes(abs_residuals: pd.Series) -> np.ndarray:
    values = abs_residuals.to_numpy(dtype=float)
    values = np.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0)
    return np.digitize(values, SEVERITY_BINS, right=False).astype(np.int64)


def _label(code: int) -> str:
    code = int(np.clip(code, 0, len(SEVERITY_LABELS) - 1))
    return SEVERITY_LABELS[code]


def _segment_ranges(flags: Sequence[bool], allow_gap: int = 1) -> list[tuple[int, int]]:
    flagged_indices = [idx for idx, value in enumerate(flags) if value]
    if not flagged_indices:
        return []
    segments: list[tuple[int, int]] = []
    start = flagged_indices[0]
    prev = flagged_indices[0]
    max_gap = allow_gap + 1
    for idx in flagged_indices[1:]:
        if idx - prev <= max_gap:
            prev = idx
            continue
        segments.append((start, prev))
        start = idx
        prev = idx
    segments.append((start, prev))
    return segments


def _infer_epoch_seconds(df: pd.DataFrame, fallback: int = EPOCH_SECONDS) -> int:
    timestamps = pd.to_datetime(df["timestamp"], errors="coerce")
    diffs = timestamps.diff().dt.total_seconds().dropna().abs()
    if diffs.empty:
        return fallback
    cadence = float(diffs.median())
    if cadence <= 0 or pd.isna(cadence):
        return fallback
    return int(round(cadence))


def detect_episodes(
    df: pd.DataFrame, include_blue: bool = False, epoch_hint: int | None = None
) -> list[Episode]:
    if df.empty:
        return []
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df.sort_values("timestamp", inplace=True)
    df.reset_index(drop=True, inplace=True)
    fallback = epoch_hint if epoch_hint is not None else EPOCH_SECONDS
    epoch_seconds = _infer_epoch_seconds(df, fallback=fallback)

    df["baseline_ns"] = _running_median(df["bias_ns"])
    df["residual_ns"] = df["bias_ns"] - df["baseline_ns"]
    df["abs_residual_ns"] = df["residual_ns"].abs()

    sigma = max(1.4826 * _median_absolute_deviation(df["residual_ns"]), 0.3)
    df["sigma_ns"] = sigma

    severity_codes = _severity_codes(df["abs_residual_ns"])
    if include_blue:
        flagged = (df["abs_residual_ns"] > BLUE_THRESHOLD).tolist()
    else:
        flagged = (severity_codes > 0).tolist()
    segments = _segment_ranges(flagged, allow_gap=0)
    episodes: list[Episode] = []
    for start_idx, end_idx in sorted(segments):
        window = df.loc[start_idx:end_idx]
        if window.empty:
            continue
        peak = float(window["abs_residual_ns"].max())
        duration = _duration_seconds(window, epoch_seconds)
        epoch_count = len(window)
        codes_slice = severity_codes[start_idx : end_idx + 1]
        max_code = int(np.max(codes_slice))
        if max_code <= 0 and not include_blue:
            continue
        promoted_code = max_code
        promoted = False
        if max_code == 2 and duration >= PROMOTION_THRESHOLD_S:
            promoted_code = 3
            promoted = True
        severity = _label(promoted_code)
        if severity == "S0" and not include_blue:
            continue
        start_ts = window.iloc[0]["timestamp"]
        end_ts = window.iloc[-1]["timestamp"]
        mean_ns = float(window["abs_residual_ns"].mean())
        episode_date = pd.Timestamp(start_ts).floor("D")
        z_max = peak / sigma if sigma > 0 else float("nan")
        episodes.append(
            Episode(
                system=str(window.iloc[0]["system"]),
                prn=str(window.iloc[0]["prn"]),
                start_utc=pd.Timestamp(start_ts),
                end_utc=pd.Timestamp(end_ts),
                duration_s=duration,
                peak_ns=peak,
                mean_ns=mean_ns,
                z_max=z_max,
                sigma_ns=float(sigma),
                epoch_seconds=epoch_seconds,
                severity=severity,
                episode_date=episode_date,
                epoch_count=epoch_count,
                max_epoch_severity=_label(max_code),
                promoted=promoted,
            )
        )
    return episodes


def _duration_seconds(window: pd.DataFrame, epoch_seconds: int) -> float:
    start = pd.Timestamp(window.iloc[0]["timestamp"])
    end = pd.Timestamp(window.iloc[-1]["timestamp"])
    return (end - start).total_seconds() + epoch_seconds
