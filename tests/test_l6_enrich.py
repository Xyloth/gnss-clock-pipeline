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


def test_l6_enrich_cli(tmp_path):
    snapshot = _backup_state()
    try:
        output_dir = tmp_path / "enriched"
        cmd = [
            sys.executable,
            "Scripts/l6_enrich.py",
            "--episodes",
            "tests/data/episodes_fixture.parquet",
            "--l3",
            "tests/data/l3_daily_fixture.parquet",
            "--output-dir",
            str(output_dir),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr
        assert "[L6_ENRICH]" in result.stdout
        outputs = list(output_dir.rglob("*.parquet"))
        assert outputs
        dataset = pd.concat(
            [pd.read_parquet(path) for path in outputs], ignore_index=True
        )
        assert not dataset.empty
        for column in [
            "beta_deg_mean_day",
            "sun_sat_earth_phase_deg_p50_day",
            "kp_max_day",
            "f10_7_day",
        ]:
            assert column in dataset.columns
            assert dataset[column].notna().mean() >= 0.9
    finally:
        _restore_state(snapshot)
