import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from corr.clk import ClockContentError, ClockDayPlan, read_clk_file
from corr.episodes import BLUE_THRESHOLD, detect_episodes

CLK_ROOT = Path(os.environ.get("CORR_CLK_ROOT", "/mnt/c/Corrections/Unzipped"))


@pytest.mark.skipif(
    not CLK_ROOT.exists(),
    reason="Real CLK data root not available; set CORR_CLK_ROOT to enable.",
)
def test_fixtures_cli_smoke(tmp_path):
    reports_root = tmp_path / "reports"
    state_path = tmp_path / "state" / "memory.jsonl"
    cmd = [
        sys.executable,
        "Scripts/build_episodes_v31.py",
        "--mode",
        "fixtures",
        "--reports-root",
        str(reports_root),
        "--clk-root",
        str(CLK_ROOT),
        "--fixtures-context",
        "tests/data/l3_fixture.parquet",
        "--state-path",
        str(state_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    output_text = result.stdout
    assert "[CHECK] R slice" in output_text
    assert "has_R09_S3=True" in output_text
    assert "[CHECK] G slice" in output_text
    assert "has_G02_long_S3=True" in output_text
    assert "[CHECK] Quiet" in output_text
    assert "S0=maybe" in output_text
    assert "[SCHEMA]" in output_text and "cols_ok=True" in output_text
    assert "epoch_seconds=30" in output_text
    assert "[DONE] tests finished" in output_text

    g_parquet = (
        reports_root
        / "L5_Episodes_v31"
        / "_fixtures"
        / "G"
        / "l5_final_episodes_v31_G_2025.parquet"
    )
    r_parquet = (
        reports_root
        / "L5_Episodes_v31"
        / "_fixtures"
        / "R"
        / "l5_final_episodes_v31_R_2025.parquet"
    )
    for path in (g_parquet, r_parquet):
        assert path.exists()

    g_df = pd.read_parquet(g_parquet)
    r_df = pd.read_parquet(r_parquet)

    r_truth = r_df[(r_df["prn"] == "R09") & (r_df["severity"] == "S3")]
    assert not r_truth.empty
    assert r_truth["peak_ns"].between(1800, 1900).any()
    assert (r_truth["duration_s"] <= 90).all()

    g_truth = g_df[(g_df["prn"] == "G02") & (g_df["severity"] == "S3")]
    assert not g_truth.empty
    assert g_truth["peak_ns"].between(200, 250).any()
    assert (g_truth["duration_s"] >= 420).all()

    quiet = g_df[(g_df["prn"] == "G03")]
    assert (
        quiet.empty or not quiet[quiet["severity"].isin(["S1", "S2", "S3"])].any().any()
    )

    context_cols = [
        "beta_deg_mean",
        "sun_sat_earth_phase_deg_p50",
        "subsat_lat_mean_deg",
        "esa_sp3_present",
        "kp_max",
        "dst_mean",
        "bz_mean",
        "vsw_mean_kms",
        "np_mean_cm3",
        "symh_mean",
        "f10_7",
    ]
    for df in (g_df, r_df):
        assert (df["epoch_seconds"] == 30).all()
        for column in context_cols:
            assert column in df.columns
        assert any(df[col].notna().any() for col in context_cols)

    assert state_path.exists()
    with state_path.open() as fh:
        lines = fh.readlines()
    assert len(lines) == 2
    entries = [json.loads(line) for line in lines]
    systems_logged = {entry["sys"] for entry in entries}
    assert systems_logged == {"G", "R"}

    mtime_before = g_parquet.stat().st_mtime
    resume_cmd = cmd + ["--resume"]
    resume_result = subprocess.run(
        resume_cmd, capture_output=True, text=True, check=False
    )
    assert resume_result.returncode == 0, resume_result.stderr
    assert g_parquet.stat().st_mtime == mtime_before
    with state_path.open() as fh:
        assert len(fh.readlines()) == 2


def test_blue_episode_is_preserved():
    timestamps = pd.date_range("2025-01-01T00:00:00Z", periods=20, freq="30s")
    bias = [0.0] * 20
    bias[8] = BLUE_THRESHOLD + 0.3
    bias[9] = BLUE_THRESHOLD + 0.1
    df = pd.DataFrame(
        {
            "system": ["G"] * 20,
            "prn": ["G99"] * 20,
            "timestamp": timestamps,
            "bias_ns": bias,
        }
    )
    episodes = detect_episodes(df, include_blue=True)
    assert any(ep.severity == "S0" for ep in episodes)


def test_system_guard_rejects_misfiled(tmp_path):
    clk_path = tmp_path / "igs99999.clk_30s"
    clk_path.write_text(
        """AS E01  2025  1  1  0  0  0.000000  1    0.100000000000E-03
AS E01  2025  1  1  0  0 30.000000  1    0.100000000000E-03
"""
    )
    plan = ClockDayPlan(system="G", day=date(2025, 1, 1), path=clk_path)
    with pytest.raises(ClockContentError):
        read_clk_file(plan)
