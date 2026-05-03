import subprocess
import sys
from pathlib import Path

import pandas as pd


def _backup_state():
    path = Path("_codex_state.json")
    return path.read_text(encoding="utf-8") if path.exists() else None


def _restore_state(snapshot):
    path = Path("_codex_state.json")
    if snapshot is None:
        if path.exists():
            path.unlink()
    else:
        path.write_text(snapshot, encoding="utf-8")


def test_bench_cli(tmp_path):
    snapshot = _backup_state()
    try:
        rapid_dir = tmp_path / "rapid"
        ultra_dir = tmp_path / "ultra"
        rapid_dir.mkdir(parents=True, exist_ok=True)
        ultra_dir.mkdir(parents=True, exist_ok=True)

        subprocess.run(
            [
                sys.executable,
                "Scripts/clk_adapters.py",
                "--source",
                "rapid",
                "--inputs",
                "tests/data/clk/rapid_fixture_20250102.clk",
                "--output-dir",
                str(rapid_dir),
                "--force",
            ],
            check=True,
        )
        subprocess.run(
            [
                sys.executable,
                "Scripts/clk_adapters.py",
                "--source",
                "ultra",
                "--inputs",
                "tests/data/clk/igu23475_18.clk",
                "--output-dir",
                str(ultra_dir),
                "--force",
            ],
            check=True,
        )

        output_dir = tmp_path / "bench"
        cmd = [
            sys.executable,
            "Scripts/bench_2025.py",
            "--final",
            "tests/data/final_fixture.parquet",
            "--rapid",
            str(next(rapid_dir.glob("*.parquet"))),
            "--ultra",
            str(next(ultra_dir.glob("*.parquet"))),
            "--output-dir",
            str(output_dir),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr
        assert "[BENCH_2025]" in result.stdout
        parquet_files = list(output_dir.glob("*.parquet"))
        assert parquet_files
        for path in parquet_files:
            df = pd.read_parquet(path)
            assert not df.empty
        assert "horizons=" in result.stdout
        assert "top5_rmse=" in result.stdout
    finally:
        _restore_state(snapshot)
