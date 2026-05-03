from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import pyarrow as pa
import pyarrow.parquet as pq

from corr.codex_state import append_codex_state
from corr.l5_schema import CANONICAL_SCHEMA, ensure_schema

SYSTEMS = ["G", "E", "R", "C"]
YEARS = range(2017, 2026)


def empty_table() -> pa.Table:
    return pa.Table.from_arrays(
        [pa.array([], type=field.type) for field in CANONICAL_SCHEMA],
        names=[field.name for field in CANONICAL_SCHEMA],
    )


def prepare_table(src_path: Path, system: str, year: int) -> pa.Table:
    if not src_path.exists():
        return add_constants(empty_table(), system, year)
    table = pq.read_table(src_path)
    if table.num_rows == 0:
        return add_constants(empty_table(), system, year)
    # Drop any extraneous columns before casting
    columns = {
        name: table[name]
        for name in table.column_names
        if name in CANONICAL_SCHEMA.names
    }
    table = pa.table(columns)
    table = ensure_schema(table)
    return add_constants(table, system, year)


def add_constants(table: pa.Table, system: str, year: int) -> pa.Table:
    length = table.num_rows
    system_array = pa.array([system] * length, type=pa.string())
    year_array = pa.array([year] * length, type=pa.int64())
    data: Dict[str, pa.Array] = {}
    for idx, field in enumerate(table.schema):
        data[field.name] = table.column(idx)
    data["system"] = system_array
    data["year"] = year_array
    reordered = [data[name] for name in CANONICAL_SCHEMA.names]
    return pa.Table.from_arrays(reordered, schema=CANONICAL_SCHEMA)


def write_table(
    table: pa.Table, hive_root: Path, years_root: Path, system: str, year: int
) -> Tuple[Path, Path]:
    hive_dir = hive_root / f"system={system}" / f"year={year}"
    hive_dir.mkdir(parents=True, exist_ok=True)
    hive_file = hive_dir / "part-0000.parquet"
    year_dir = years_root / system
    year_dir.mkdir(parents=True, exist_ok=True)
    year_file = year_dir / f"l5_final_episodes_v31_{system}_{year}.parquet"
    pq.write_table(table, hive_file, use_dictionary=False)
    pq.write_table(table, year_file, use_dictionary=False)
    return hive_file, year_file


def normalize(
    source_root: Path,
    hive_root: Path,
    years_root: Path,
    manifest_path: Path,
) -> Dict[Tuple[str, int], Dict[str, object]]:
    hive_root.mkdir(parents=True, exist_ok=True)
    years_root.mkdir(parents=True, exist_ok=True)
    manifest: Dict[Tuple[str, int], Dict[str, object]] = {}
    for system in SYSTEMS:
        for year in YEARS:
            src_file = (
                source_root / system / f"l5_final_episodes_v31_{system}_{year}.parquet"
            )
            table = prepare_table(src_file, system, year)
            write_table(table, hive_root, years_root, system, year)
            epoch_seconds = (
                set(map(int, table.column("epoch_seconds").to_pylist()))
                if table.num_rows
                else set()
            )
            severity_counts = (
                Counter(table.column("severity").to_pylist())
                if table.num_rows
                else Counter()
            )
            manifest[(system, year)] = {
                "rows": int(table.num_rows),
                "epoch_seconds": sorted(epoch_seconds),
                "severity": {key: int(value) for key, value in severity_counts.items()},
            }
            print(
                f"[NORMALIZE] {system} {year} rows={table.num_rows} "
                f"epoch_seconds={sorted(epoch_seconds)} severity={dict(severity_counts)}"
            )
    total_rows = sum(entry["rows"] for entry in manifest.values())
    anomalies = [
        (sys, year)
        for (sys, year), entry in manifest.items()
        if entry["rows"] > 0 and entry["epoch_seconds"] != [30]
    ]
    print(f"[NORMALIZE] TOTAL rows={total_rows} anomalies={anomalies}")
    manifest_data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "entries": [
            {
                "system": system,
                "year": year,
                "rows": info["rows"],
                "epoch_seconds": info["epoch_seconds"],
                "severity": info["severity"],
            }
            for (system, year), info in sorted(manifest.items())
        ],
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")
    append_codex_state(
        "normalize_l5_v31",
        {
            "source_root": str(source_root),
            "hive_root": str(hive_root),
            "years_root": str(years_root),
        },
        outputs=[
            str(hive_root),
            str(years_root),
            str(manifest_path),
        ],
        metrics={
            "total_rows": total_rows,
            "anomalies": anomalies,
        },
    )
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser("Normalize existing L5 v31 production outputs.")
    parser.add_argument(
        "--source-root",
        default="/mnt/c/Corrections/Reports/L5_Episodes_v31_PROD/L5_Episodes_v31",
        type=Path,
    )
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
    parser.add_argument(
        "--manifest-path",
        default="/mnt/c/Corrections/Reports/L5_Episodes_v31_HIVE/_manifest.json",
        type=Path,
    )
    return parser


def main(argv: List[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    normalize(args.source_root, args.hive_root, args.years_root, args.manifest_path)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
