from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds

from corr.episodes import detect_episodes


def clk_filename(date: datetime) -> str:
    doy = date.timetuple().tm_yday
    return f"igs{doy:03d}{date.year % 100:02d}.clk_30s"


def parse_clk(path: Path, prn: str) -> pd.DataFrame:
    rows: List[Tuple[str, str, pd.Timestamp, float]] = []
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if not line.startswith("AS "):
                continue
            parts = line.split()
            if len(parts) < 10:
                continue
            line_prn = parts[1]
            if line_prn != prn:
                continue
            year, month, day, hour, minute = map(int, parts[2:7])
            second = float(parts[7])
            ts = pd.Timestamp(datetime(year, month, day, hour, minute, int(second)))
            bias_sec = float(parts[9])
            rows.append(("G", prn, ts, bias_sec * 1e9))
    if not rows:
        return pd.DataFrame(columns=["system", "prn", "timestamp", "bias_ns"])
    df = pd.DataFrame(rows, columns=["system", "prn", "timestamp", "bias_ns"])
    return df


def episodes_from_clk(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(
            columns=[
                "system",
                "prn",
                "start_utc",
                "end_utc",
                "duration_s",
                "peak_ns",
                "mean_ns",
                "z_max",
                "sigma_ns",
                "severity",
                "episode_date",
            ]
        )
    episodes = detect_episodes(df, include_blue=True)
    records = []
    for ep in episodes:
        records.append(
            {
                "system": ep.system,
                "prn": ep.prn,
                "start_utc": ep.start_utc,
                "end_utc": ep.end_utc,
                "duration_s": ep.duration_s,
                "peak_ns": ep.peak_ns,
                "mean_ns": ep.mean_ns,
                "z_max": ep.z_max,
                "sigma_ns": ep.sigma_ns,
                "severity": ep.severity,
                "episode_date": ep.episode_date,
            }
        )
    return pd.DataFrame(records)


def hive_lookup(hive_root: Path, system: str, prn: str, date: datetime) -> pd.DataFrame:
    partition_schema = pa.schema([("system", pa.string()), ("year", pa.int64())])
    dataset = ds.dataset(
        str(hive_root),
        format="parquet",
        partitioning=ds.partitioning(partition_schema, flavor="hive"),
    )
    table = dataset.to_table(
        filter=(ds.field("system") == system)
        & (ds.field("prn") == prn)
        & (ds.field("year") == date.year),
    )
    if table.num_rows == 0:
        return pd.DataFrame(columns=table.column_names)
    df = table.to_pandas()
    df["episode_date"] = pd.to_datetime(df["start_utc"]).dt.floor("D")
    return df[df["episode_date"] == date.normalize()]


def compare(clk_df: pd.DataFrame, hive_df: pd.DataFrame) -> Tuple[bool, List[Tuple]]:
    clk_set = {
        (
            row.start_utc,
            row.end_utc,
            round(row.peak_ns, 6),
            row.severity,
        )
        for row in clk_df.itertuples()
    }
    hive_set = {
        (
            row.start_utc,
            row.end_utc,
            round(row.peak_ns, 6),
            row.severity,
        )
        for row in hive_df.itertuples()
    }
    diffs = list(sorted(clk_set.symmetric_difference(hive_set)))[:5]
    return clk_set == hive_set, diffs


def parse_checks(values: List[str] | None) -> List[Tuple[str, datetime]]:
    if not values:
        return [
            ("G05", datetime(2017, 8, 31)),
            ("G30", datetime(2018, 2, 13)),
            ("G16", datetime(2020, 8, 6)),
        ]
    parsed = []
    for item in values:
        prn, date_str = item.split(":", 1)
        parsed.append((prn.strip(), datetime.fromisoformat(date_str.strip())))
    return parsed


def run_checks(
    clk_root: Path, hive_root: Path, checks: List[Tuple[str, datetime]]
) -> None:
    for prn, date in checks:
        filename = clk_filename(date)
        clk_path = clk_root / "Final" / str(date.year) / filename
        df = parse_clk(clk_path, prn)
        clk_eps = episodes_from_clk(df)
        hive_eps = hive_lookup(hive_root, "G", prn, pd.Timestamp(date))
        match, diffs = compare(clk_eps, hive_eps)
        print(
            f"[TRIAGE] {prn} {date.date()} found_in_clk={len(clk_eps)} "
            f"found_in_hive={len(hive_eps)} match={match} diffs={diffs}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        "GPS triage spot-checks against normalized HIVE dataset."
    )
    parser.add_argument(
        "--clk-root",
        default="/mnt/c/Corrections/Unzipped/G",
        type=Path,
    )
    parser.add_argument(
        "--hive-root",
        default="/mnt/c/Corrections/Reports/L5_Episodes_v31_HIVE",
        type=Path,
    )
    parser.add_argument(
        "--checks",
        nargs="+",
        help="Optional PRN:YYYY-MM-DD pairs for targeted triage (default uses built-in trio).",
    )
    return parser


def main(argv: List[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_checks(args.clk_root, args.hive_root, parse_checks(args.checks))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
