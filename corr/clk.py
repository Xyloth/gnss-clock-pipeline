from __future__ import annotations

import gzip
import shutil
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd


class ClockContentError(RuntimeError):
    """Raised when a CLK file fails system validation."""


SYSTEM_PREFIXES = {"G", "R", "E", "C", "J"}
MIN_AS_LINES = 10


@dataclass(frozen=True)
class ClockDayPlan:
    system: str
    day: date
    path: Path
    prn_filter: Sequence[str] | None = None
    synthetic: bool = False

    @property
    def year(self) -> int:
        return self.day.year


def _parse_as_line(line: str) -> tuple[str, datetime, float] | None:
    if not line.startswith("AS "):
        return None
    parts = line.split()
    if len(parts) < 10:
        return None
    prn = parts[1]
    if not prn or prn[0] not in SYSTEM_PREFIXES:
        return None
    try:
        year, month, day, hour, minute = map(int, parts[2:7])
        second = int(float(parts[7]))
        ts = datetime(year, month, day, hour, minute, second)
        bias_seconds = float(parts[9])
    except (ValueError, TypeError):
        return None
    return prn, ts, bias_seconds * 1e9


def read_clk_file(
    plan: ClockDayPlan, min_prns: int = 10, min_epochs_per_prn: int = 2000
) -> pd.DataFrame:
    system_letter = plan.system
    per_prn_counts: Counter[str] = Counter()
    records: list[tuple[str, datetime, float]] = []
    with plan.path.open() as fh:
        for line in fh:
            parsed = _parse_as_line(line)
            if parsed is None:
                continue
            prn, ts, bias_ns = parsed
            if prn[0] == system_letter:
                per_prn_counts[prn] += 1
                if plan.prn_filter is not None and prn not in plan.prn_filter:
                    continue
                records.append((system_letter, prn, ts, bias_ns))
    if not per_prn_counts:
        raise ClockContentError(f"No AS records in {plan.path}")
    prn_count = len(per_prn_counts)
    if prn_count < min_prns:
        raise ClockContentError(
            f"{plan.path} failed system content check (prns={prn_count} < {min_prns})"
        )
    total_epochs = sum(per_prn_counts.values())
    if total_epochs < min_epochs_per_prn * prn_count:
        raise ClockContentError(
            f"{plan.path} failed coverage check ({total_epochs} < {min_epochs_per_prn * prn_count})"
        )
    if not records:
        return pd.DataFrame(columns=["system", "prn", "timestamp", "bias_ns"])
    df = pd.DataFrame(records, columns=["system", "prn", "timestamp", "bias_ns"])
    df.sort_values("timestamp", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def clk_path_for(system: str, day: date, root: Path) -> Path:
    year = day.year
    doy = day.timetuple().tm_yday
    yy = year % 100
    day_dir = root / system / "Final" / f"{year}"
    candidates = [
        day_dir / f"igs{doy:03d}{yy:02d}.clk_30s",
    ]
    pattern = f"*_{year}{doy:03d}0000_01D_30S_CLK.CLK"
    candidates.extend(sorted(day_dir.glob(pattern)))
    candidates.extend(sorted(day_dir.glob(pattern + ".gz")))

    for candidate in candidates:
        plain_path = _ensure_plain_clock(candidate)
        if plain_path is None or not plain_path.exists():
            continue
        info = _verify_plain_clock(system, year, plain_path)
        if info["ok"]:
            print(
                f"[CLK] {system} {day.isoformat()} -> {plain_path.name} (AS {system}={info.get('sat_lines')})"
            )
            return plain_path
    raise FileNotFoundError(
        f"No valid CLK file found for {system} {day.isoformat()} in {day_dir}"
    )


def _ensure_plain_clock(path: Path) -> Path | None:
    if not path.exists():
        return None
    if path.suffix != ".gz":
        return path
    plain = path.with_suffix("")
    try:
        if not plain.exists() or plain.stat().st_mtime < path.stat().st_mtime:
            with gzip.open(path, "rb") as src, plain.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    except Exception:
        return None
    return plain if plain.exists() else None


def _verify_plain_clock(system: str, year: int, path: Path) -> dict[str, object]:
    result = {
        "ok": False,
        "sat_lines": 0,
        "span_start": None,
        "span_end": None,
        "note": "",
    }
    try:
        with path.open("rb") as fb:
            head = fb.read(512).lstrip()
        if head.startswith(b"<") and (b"<html" in head.lower() or b"<!doctype" in head.lower()):
            result["note"] = "html payload"
            return result
    except Exception as exc:
        result["note"] = f"read error: {exc}"
        return result

    header_ok = False
    clock_ok = False
    as_count = 0
    year_ok = False
    first_ts: datetime | None = None
    last_ts: datetime | None = None

    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.rstrip("\n")
                if "RINEX" in line:
                    header_ok = True
                if "CLOCK" in line:
                    clock_ok = True
                if line.startswith("AS "):
                    parts = line.split()
                    if len(parts) < 3 or not parts[1].startswith(system):
                        continue
                    as_count += 1
                    ts = _parse_as_timestamp(parts)
                    if ts is not None:
                        if first_ts is None or ts < first_ts:
                            first_ts = ts
                        if last_ts is None or ts > last_ts:
                            last_ts = ts
                        if ts.year == year:
                            year_ok = True
                    else:
                        try:
                            if int(parts[2]) == year:
                                year_ok = True
                        except ValueError:
                            pass
                    if as_count >= 100 and header_ok and year_ok:
                        break
                elif line.startswith("* ") and not year_ok:
                    ts = _parse_epoch_timestamp(line)
                    if ts is not None and ts.year == year:
                        year_ok = True
    except Exception as exc:
        result["note"] = f"read error: {exc}"
        return result

    result.update(
        {
            "sat_lines": as_count,
            "span_start": first_ts.isoformat() if first_ts else None,
            "span_end": last_ts.isoformat() if last_ts else None,
        }
    )
    ok = header_ok and clock_ok and as_count >= MIN_AS_LINES and year_ok
    result["ok"] = ok
    if not ok:
        status = []
        if not header_ok:
            status.append("missing header")
        if not clock_ok:
            status.append("no CLOCK tag")
        if as_count < MIN_AS_LINES:
            status.append(f"AS count {as_count}")
        if not year_ok:
            status.append("year mismatch")
        result["note"] = "; ".join(status)
    else:
        result["note"] = f"AS count {as_count}"
    return result


def _parse_as_timestamp(parts: Sequence[str]) -> datetime | None:
    if len(parts) < 8:
        return None
    try:
        year, month, day = map(int, parts[2:5])
        hour = int(parts[5])
        minute = int(parts[6])
        second = float(parts[7])
        sec_int = int(second)
        micro = int(round((second - sec_int) * 1_000_000))
        return datetime(year, month, day, hour, minute, sec_int, micro)
    except (ValueError, TypeError):
        return None


def _parse_epoch_timestamp(line: str) -> datetime | None:
    tokens = line[2:].split()
    if len(tokens) < 6:
        return None
    try:
        year, month, day = map(int, tokens[:3])
        hour, minute = map(int, tokens[3:5])
        second = float(tokens[5])
        sec_int = int(second)
        micro = int(round((second - sec_int) * 1_000_000))
        return datetime(year, month, day, hour, minute, sec_int, micro)
    except (ValueError, TypeError):
        return None
