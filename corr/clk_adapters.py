from __future__ import annotations

import math
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from .codex_state import append_codex_state


@dataclass
class AdapterResult:
    source: str
    files: list[Path]
    outputs: list[Path]
    rows: int
    epoch_hist: dict[int, int]
    elapsed_s: float


def parse_rinex_clock(path: Path, source: str) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.startswith("AS "):
                continue
            parts = line.split()
            if len(parts) < 10:
                continue
            prn = parts[1]
            system = prn[0]
            try:
                year, month, day, hour, minute = map(int, parts[2:7])
                seconds = float(parts[7])
                whole = int(seconds)
                fractional = seconds - whole
                microseconds = int(round(fractional * 1_000_000))
                ts = datetime(year, month, day, hour, minute, whole, microseconds)
                bias_seconds = float(parts[9])
            except (ValueError, TypeError):
                continue
            records.append(
                {
                    "system": system,
                    "prn": prn,
                    "ts_utc": pd.Timestamp(ts),
                    "bias_ns": bias_seconds * 1e9,
                }
            )

    df = pd.DataFrame(records)
    if df.empty:
        return df

    df.sort_values(["prn", "ts_utc"], inplace=True)
    epoch_seconds_map: dict[str, int] = {}
    for prn, group in df.groupby("prn"):
        deltas = group["ts_utc"].diff().dropna()
        if deltas.empty:
            epoch_seconds_map[prn] = 0
            continue
        seconds = deltas.dt.total_seconds()
        # Prefer the mode to tolerate outliers
        counts = seconds.round().astype(int).value_counts()
        epoch = int(counts.idxmax()) if not counts.empty else int(seconds.median())
        epoch_seconds_map[prn] = epoch
    df["epoch_seconds"] = df["prn"].map(epoch_seconds_map).astype(int)
    df["source"] = source
    return df.reset_index(drop=True)


def infer_issue_time(path: Path, source: str) -> pd.Timestamp | None:
    if source != "ultra":
        return None
    name = path.stem.lower()
    import re
    match = re.match(r'(?:gbm0mgxrap|grg0opsult|cod0mgxult)_(\d{4})(\d{3})(\d{2})00', name.upper())
    if match:
        year = int(match.group(1))
        doy = int(match.group(2))
        hour = int(match.group(3))
        base = datetime(year, 1, 1) + timedelta(days=doy - 1)
        issue_time = base + timedelta(hours=hour)
        return pd.Timestamp(issue_time, tz=timezone.utc)
    if not name.startswith("igu") or "_" not in name:
        return None
    try:
        prefix, hour_part = name.split("_", 1)
        gps_week = int(prefix[3:7])
        gps_day = int(prefix[7])
        hour = int(hour_part[:2])
    except (ValueError, IndexError):
        return None
    gps_epoch = datetime(1980, 1, 6)
    issue_time = gps_epoch + timedelta(weeks=gps_week, days=gps_day, hours=hour)
    return pd.Timestamp(issue_time)


def attach_issue_time(
    df: pd.DataFrame, issued_at: pd.Timestamp | None
) -> pd.DataFrame:
    if df.empty:
        return df
    if issued_at is None:
        df["issued_at"] = pd.NaT
        df["lead_time_seconds"] = pd.Series([math.nan] * len(df), dtype="float64")
        return df
    min_ts = df["ts_utc"].min()
    if issued_at > min_ts:
        issued_at = min_ts
    df["issued_at"] = issued_at
    lead = (df["ts_utc"] - issued_at).dt.total_seconds()
    df["lead_time_seconds"] = lead.astype(float)
    return df


def run_clk_adapter(
    source: str, inputs: Iterable[Path], output_dir: Path, force: bool = False
) -> AdapterResult:
    start = time.time()
    outputs: list[Path] = []
    frames: list[pd.DataFrame] = []
    files: list[Path] = []
    output_dir.mkdir(parents=True, exist_ok=True)

    for path in inputs:
        df = parse_rinex_clock(path, source)
        issued_at = infer_issue_time(path, source)
        df = attach_issue_time(df, issued_at)
        if df.empty:
            continue
        files.append(path)
        combined = df
        out_path = output_dir / f"{path.stem}.parquet"
        if out_path.exists() and not force:
            existing = pd.read_parquet(out_path)
            combined = pd.concat([existing, df], ignore_index=True).drop_duplicates(
                subset=["system", "prn", "ts_utc"]
            )
        combined.to_parquet(out_path, index=False)
        outputs.append(out_path)
        frames.append(combined)

    if frames:
        merged = pd.concat(frames, ignore_index=True)
    else:
        merged = pd.DataFrame(
            columns=["system", "prn", "ts_utc", "bias_ns", "epoch_seconds", "source"]
        )
    hist = (
        merged.groupby(["source", "epoch_seconds"]).size().to_dict()
        if not merged.empty
        else {}
    )
    elapsed = time.time() - start
    rows = int(len(merged))
    return AdapterResult(
        source=source,
        files=files,
        outputs=outputs,
        rows=rows,
        epoch_hist={
            int(k[1]) if isinstance(k, tuple) else int(k): int(v)
            for k, v in hist.items()
        },
        elapsed_s=elapsed,
    )


def print_adapter_metrics(result: AdapterResult) -> None:
    hist_parts = ", ".join(
        f"{key}:{value}" for key, value in sorted(result.epoch_hist.items())
    )
    outputs_str = (
        ", ".join(str(path) for path in result.outputs) if result.outputs else "none"
    )
    print(
        "[CLK_ADAPTER]\n"
        f"source={result.source} files={len(result.files)} rows={result.rows}\n"
        f"epoch_seconds_hist={{{hist_parts}}}\n"
        f"outputs={outputs_str}\n"
        f"elapsed={math.ceil(result.elapsed_s)}s"
    )


def record_adapter_state(result: AdapterResult, args: dict) -> None:
    metrics = {
        "rows": result.rows,
        "epoch_seconds_hist": result.epoch_hist,
        "elapsed_s": result.elapsed_s,
    }
    append_codex_state(
        "clk_adapters", args, [str(path) for path in result.outputs], metrics
    )
