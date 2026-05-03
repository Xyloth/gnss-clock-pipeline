import subprocess
import sys
from pathlib import Path

import pandas as pd


def _backup_state():
    path = Path("_codex_state.json")
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def _restore_state(snapshot):
    path = Path("_codex_state.json")
    if snapshot is None:
        if path.exists():
            path.unlink()
    else:
        path.write_text(snapshot, encoding="utf-8")


def test_clk_adapters_rapid_and_ultra(tmp_path):
    state_snapshot = _backup_state()
    try:
        output_dir = tmp_path / "Reports" / "DEV_Rapid"
        cmd = [
            sys.executable,
            "Scripts/clk_adapters.py",
            "--source",
            "rapid",
            "--inputs",
            "tests/data/clk/rapid_fixture_20250102.clk",
            "--output-dir",
            str(output_dir),
            "--force",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr
        assert "[CLK_ADAPTER]" in result.stdout
        adapter_output = next(output_dir.glob("*.parquet"))
        df = pd.read_parquet(adapter_output)
        assert len(df) >= 1000
        assert set(
            ["system", "prn", "ts_utc", "bias_ns", "epoch_seconds", "source"]
        ).issubset(df.columns)
        assert df["epoch_seconds"].iloc[0] == 300
        assert (df["source"] == "rapid").all()

        ultra_dir = tmp_path / "Reports" / "DEV_Ultra"
        ultra_cmd = [
            sys.executable,
            "Scripts/clk_adapters.py",
            "--source",
            "ultra",
            "--inputs",
            "tests/data/clk/igu23475_18.clk",
            "--output-dir",
            str(ultra_dir),
            "--force",
        ]
        ultra = subprocess.run(ultra_cmd, capture_output=True, text=True, check=False)
        assert ultra.returncode == 0, ultra.stderr
        assert "epoch_seconds_hist" in ultra.stdout
        ultra_output = next(ultra_dir.glob("*.parquet"))
        udf = pd.read_parquet(ultra_output)
        assert len(udf) >= 1000
        assert (udf["source"] == "ultra").all()
        assert "lead_time_seconds" in udf.columns
        assert udf["lead_time_seconds"].notna().any()
    finally:
        _restore_state(state_snapshot)
