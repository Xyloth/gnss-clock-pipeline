from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple

import pyarrow as pa
import pyarrow.parquet as pq

from corr.l5_schema import CANONICAL_SCHEMA


def expected_severity(peak_ns: float, duration_s: float) -> str:
    if not isinstance(peak_ns, (int, float)):
        peak_ns = float(peak_ns or 0.0)
    if not isinstance(duration_s, (int, float)):
        duration_s = float(duration_s or 0.0)
    if peak_ns < 0.5:
        code = 0
    elif peak_ns < 3.0:
        code = 1
    elif peak_ns < 5.0:
        code = 2
    else:
        code = 3
    if code > 0 and duration_s >= 120.0:
        code = min(3, code + 1)
    return f"S{code}"


def per_year_file(path_root: Path, system: str, year: int) -> Path:
    return path_root / system / f"l5_final_episodes_v31_{system}_{year}.parquet"


def analyze_table(table: pa.Table) -> Tuple[Counter, List[str]]:
    severity_counts = Counter(table.column("severity").to_pylist())
    epoch_seconds = set(
        int(v) for v in table.column("epoch_seconds").to_pylist() if v is not None
    )
    anomalies = []
    if epoch_seconds and epoch_seconds != {30}:
        anomalies.append(f"epoch_seconds={sorted(epoch_seconds)}")
    computed = []
    peaks = table.column("peak_ns").to_pylist()
    durations = table.column("duration_s").to_pylist()
    for peak, duration in zip(peaks, durations):
        computed.append(expected_severity(float(peak), float(duration)))
    actual = table.column("severity").to_pylist()
    mismatches = [idx for idx, (a, b) in enumerate(zip(actual, computed)) if a != b]
    if mismatches:
        sample_rows = table.take(pa.array(mismatches[:5]))
        anomalies.append(f"severity_mismatch={sample_rows.to_pylist()}")
    return severity_counts, anomalies


def compare_year_totals(
    table: pa.Table, materialized: Path
) -> Tuple[bool, Dict[str, float]]:
    counts = Counter(table.column("severity").to_pylist())
    total = table.num_rows
    if not materialized.exists():
        return False, {"missing_file": 1.0}
    year_table = pq.read_table(materialized)
    year_counts = (
        Counter(year_table.column("severity").to_pylist())
        if year_table.num_rows
        else Counter()
    )
    year_total = year_table.num_rows
    differences: Dict[str, float] = {}
    for key in set(counts) | set(year_counts):
        a = counts.get(key, 0)
        b = year_counts.get(key, 0)
        if max(a, b) == 0:
            continue
        diff = abs(a - b) / max(a, b)
        if diff > 0.01:
            differences[f"severity_{key}"] = diff
    if max(total, year_total) > 0:
        total_diff = abs(total - year_total) / max(total, year_total)
        if total_diff > 0.01:
            differences["total"] = total_diff
    return len(differences) == 0, differences


def validate(hive_root: Path, years_root: Path) -> Tuple[bool, List[str]]:
    if not hive_root.exists():
        raise FileNotFoundError(hive_root)
    offending: List[str] = []
    total_rows = 0
    for system_dir in sorted(hive_root.glob("system=*")):
        system = system_dir.name.split("=", 1)[1]
        for year_dir in sorted(system_dir.glob("year=*")):
            year = int(year_dir.name.split("=", 1)[1])
            dataset = pq.ParquetFile(year_dir / "part-0000.parquet").read(
                columns=CANONICAL_SCHEMA.names
            )
            total_rows += dataset.num_rows
            severity_counts, anomalies = analyze_table(dataset)
            ok, differences = compare_year_totals(
                dataset, per_year_file(years_root, system, year)
            )
            if anomalies:
                offending.append(f"{system} {year} anomalies: {anomalies}")
            if not ok:
                offending.append(f"{system} {year} count_diff: {differences}")
    status = "OK" if not offending else "FAIL"
    print(f"[VALIDATION] {status} total_rows={total_rows}")
    for line in offending[:10]:
        print(f"[VALIDATION] {line}")
    return len(offending) == 0, offending


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser("Validate normalized L5 v31 dataset.")
    parser.add_argument(
        "--hive-root",
        default="/mnt/c/Corrections/Reports/L5_Episodes_v31_HIVE",
        type=Path,
    )
    parser.add_argument(
        "--years-root",
        default="/mnt/c/Corrections/Reports/L5_Episodes_v31_YEARS",
        type=Path,
    )
    return parser


def main(argv: List[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ok, _ = validate(args.hive_root, args.years_root)
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
