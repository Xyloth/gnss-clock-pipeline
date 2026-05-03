import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from corr.l5_schema import CANONICAL_SCHEMA


def write_sample_source(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for system in ["G", "E", "R", "C"]:
        (root / system).mkdir(exist_ok=True)
    columns = {
        "system": pa.array(["G", "G"], type=pa.string()),
        "year": pa.array([2017, 2017], type=pa.int64()),
        "prn": pa.array(["G01", "G01"], type=pa.string()),
        "severity": pa.array(["S3", "S2"], type=pa.string()),
        "max_epoch_severity": pa.array(["S3", "S2"], type=pa.string()),
        "epoch_count": pa.array([10, 5], type=pa.int64()),
        "promoted": pa.array([False, False], type=pa.bool_()),
        "epoch_seconds": pa.array([30, 30], type=pa.int64()),
        "start_utc": pa.array(
            [datetime(2017, 1, 1, 0, 0), datetime(2017, 1, 1, 0, 10)],
            type=pa.timestamp("ns"),
        ),
        "end_utc": pa.array(
            [datetime(2017, 1, 1, 0, 5), datetime(2017, 1, 1, 0, 11, 30)],
            type=pa.timestamp("ns"),
        ),
        "duration_s": pa.array([300.0, 90.0], type=pa.float64()),
        "peak_ns": pa.array([25.0, 4.0], type=pa.float64()),
        "mean_ns": pa.array([20.0, 2.0], type=pa.float64()),
        "sigma_ns": pa.array([1.0, 1.0], type=pa.float64()),
        "z_max": pa.array([25.0, 4.0], type=pa.float64()),
    }
    table = pa.Table.from_arrays(list(columns.values()), names=list(columns.keys()))
    pq.write_table(table, root / "G" / "l5_final_episodes_v31_G_2017.parquet")
    empty_arrays = [pa.array([], type=field.type) for field in CANONICAL_SCHEMA]
    empty = pa.Table.from_arrays(empty_arrays, names=CANONICAL_SCHEMA.names)
    pq.write_table(empty, root / "G" / "l5_final_episodes_v31_G_2018.parquet")


def test_normalize_cli(tmp_path):
    source_root = tmp_path / "source"
    write_sample_source(source_root)
    hive_root = tmp_path / "hive"
    years_root = tmp_path / "years"
    manifest_path = tmp_path / "hive" / "_manifest.json"
    cmd = [
        sys.executable,
        "Scripts/normalize_l5_v31.py",
        "--source-root",
        str(source_root),
        "--hive-root",
        str(hive_root),
        "--years-root",
        str(years_root),
        "--manifest-path",
        str(manifest_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    assert (hive_root / "system=G" / "year=2017" / "part-0000.parquet").exists()
    table = pq.ParquetFile(
        hive_root / "system=G" / "year=2017" / "part-0000.parquet"
    ).read()
    assert table.schema == CANONICAL_SCHEMA
    manifest = json.loads(manifest_path.read_text())
    entry = next(
        item
        for item in manifest["entries"]
        if item["system"] == "G" and item["year"] == 2017
    )
    assert entry["rows"] == 2
    assert "[NORMALIZE]" in result.stdout


def test_validate_cli(tmp_path):
    source_root = tmp_path / "source"
    write_sample_source(source_root)
    hive_root = tmp_path / "hive"
    years_root = tmp_path / "years"
    manifest_path = tmp_path / "hive" / "_manifest.json"
    subprocess.run(
        [
            sys.executable,
            "Scripts/normalize_l5_v31.py",
            "--source-root",
            str(source_root),
            "--hive-root",
            str(hive_root),
            "--years-root",
            str(years_root),
            "--manifest-path",
            str(manifest_path),
        ],
        check=True,
    )
    result = subprocess.run(
        [
            sys.executable,
            "Scripts/validate_l5_v31.py",
            "--hive-root",
            str(hive_root),
            "--years-root",
            str(years_root),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "[VALIDATION] OK" in result.stdout


def create_triage_fixture(root: Path, hive_root: Path):
    clk_dir = root / "Final" / "2017"
    clk_dir.mkdir(parents=True, exist_ok=True)
    path = clk_dir / "igs24317.clk_30s"
    lines = [
        "     3.00           C                                       RINEX VERSION / TYPE",
        "CLKGEN              TEST                                     PGM / RUN BY / DATE ",
        "    18                                                      LEAP SECONDS        ",
        "     2    AR    AS                                          # / TYPES OF DATA   ",
    ]
    ts = datetime(2017, 8, 31, 0, 0)
    rows = []
    biases = [0.0] * 31
    for i in range(10, 15):
        biases[i] = 8e-9
    for idx, bias in enumerate(biases):
        stamp = ts + idx * pd.Timedelta(seconds=30)
        lines.append(
            f"AS G05  {stamp.year:4d} {stamp.month:2d} {stamp.day:2d} {stamp.hour:2d} {stamp.minute:2d} {stamp.second:2d}.000000  1    {bias: .12e}  0.000000000000e+00"
        )
        rows.append(
            {"system": "G", "prn": "G05", "timestamp": stamp, "bias_ns": bias * 1e9}
        )
    path.write_text("\n".join(lines), encoding="utf-8")
    from corr.episodes import detect_episodes

    clk_df = pd.DataFrame(rows)
    episodes = detect_episodes(clk_df, include_blue=True)
    records = []
    for ep in episodes:
        records.append(
            {
                "system": ep.system,
                "year": 2017,
                "prn": ep.prn,
                "severity": ep.severity,
                "max_epoch_severity": ep.max_epoch_severity,
                "epoch_count": ep.epoch_count,
                "promoted": ep.promoted,
                "epoch_seconds": 30,
                "start_utc": ep.start_utc,
                "end_utc": ep.end_utc,
                "duration_s": ep.duration_s,
                "peak_ns": ep.peak_ns,
                "mean_ns": ep.mean_ns,
                "sigma_ns": ep.sigma_ns,
                "z_max": ep.z_max,
            }
        )
    df = pd.DataFrame(records)
    hive_dir = hive_root / "system=G" / "year=2017"
    hive_dir.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.Table.from_pandas(df, schema=CANONICAL_SCHEMA, preserve_index=False),
        hive_dir / "part-0000.parquet",
    )


def test_triage_cli(tmp_path):
    clk_root = tmp_path / "clk"
    hive_root = tmp_path / "hive"
    create_triage_fixture(clk_root, hive_root)
    result = subprocess.run(
        [
            sys.executable,
            "Scripts/triage_gps_years.py",
            "--clk-root",
            str(clk_root),
            "--hive-root",
            str(hive_root),
            "--checks",
            "G05:2017-08-31",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "[TRIAGE]" in result.stdout
