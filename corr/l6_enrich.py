from __future__ import annotations

import math
import time
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

import pandas as pd

from .codex_state import append_codex_state

GEOM_COLUMNS = [
    "beta_deg_mean_day",
    "sun_sat_earth_phase_deg_p50_day",
    "subsat_lat_mean_deg_day",
    "esa_sp3_present_day",
]

SPACE_COLUMNS = [
    "kp_max_day",
    "dst_mean_day",
    "bz_mean_day",
    "vsw_mean_kms_day",
    "np_mean_cm3_day",
    "symh_mean_day",
    "f10_7_day",
]


class EnrichmentResult:
    def __init__(
        self,
        outputs: list[Path],
        episodes: int,
        join_coverage: float,
        non_null: dict[str, float],
        severity_hist: dict[str, int],
        elapsed_s: float,
    ) -> None:
        self.outputs = outputs
        self.episodes = episodes
        self.join_coverage = join_coverage
        self.non_null = non_null
        self.severity_hist = severity_hist
        self.elapsed_s = elapsed_s


def load_parquet(paths: Iterable[Path]) -> pd.DataFrame:
    frames = [pd.read_parquet(path) for path in paths if path.exists()]
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    return df


def run_enrichment(
    episodes_paths: Iterable[Path],
    l3_path: Path,
    output_dir: Path,
) -> EnrichmentResult:
    start = time.time()
    output_dir.mkdir(parents=True, exist_ok=True)

    episodes_df = load_parquet(episodes_paths)
    if episodes_df.empty:
        raise ValueError("No episodes data found.")
    episodes_df["episode_date"] = pd.to_datetime(
        episodes_df["episode_date"]
    ).dt.normalize()

    l3_df = pd.read_parquet(l3_path)
    l3_df["date"] = pd.to_datetime(l3_df["date"]).dt.normalize()

    enriched = episodes_df.merge(
        l3_df,
        left_on=["system", "prn", "episode_date"],
        right_on=["system", "prn", "date"],
        how="left",
        suffixes=("", "_l3"),
    )
    enriched = (
        enriched.drop(columns=["date"]) if "date" in enriched.columns else enriched
    )

    coverage = (
        0.0
        if enriched.empty
        else float(
            (enriched[GEOM_COLUMNS + SPACE_COLUMNS].notna().any(axis=1).sum())
            / len(enriched)
        )
    )
    non_null = {
        column: (
            0.0 if enriched.empty else float(enriched[column].notna().mean() * 100.0)
        )
        for column in GEOM_COLUMNS + SPACE_COLUMNS
    }
    severity_hist = Counter(
        enriched.get("severity", pd.Series(dtype=str))
    ).most_common()
    severity_dict = {key: int(value) for key, value in severity_hist}

    outputs: list[Path] = []
    for (system, year), group in enriched.groupby(
        [enriched["system"], enriched["episode_date"].dt.year]
    ):
        system_dir = output_dir / str(system)
        system_dir.mkdir(parents=True, exist_ok=True)
        out_path = system_dir / f"l6_enriched_v31_{system}_{year}.parquet"
        group.to_parquet(out_path, index=False)
        outputs.append(out_path)

    elapsed = time.time() - start
    return EnrichmentResult(
        outputs=outputs,
        episodes=len(enriched),
        join_coverage=coverage * 100.0,
        non_null=non_null,
        severity_hist=severity_dict,
        elapsed_s=elapsed,
    )


def print_enrichment_metrics(result: EnrichmentResult) -> None:
    non_null_fmt = ", ".join(
        f"{col}:{pct:.1f}" for col, pct in sorted(result.non_null.items())
    )
    severity_fmt = ", ".join(
        f"{sev}:{count}" for sev, count in sorted(result.severity_hist.items())
    )
    print(
        "[L6_ENRICH]\n"
        f"episodes={result.episodes}\n"
        f"join_coverage={result.join_coverage:.1f}%\n"
        f"non_null%={{{non_null_fmt}}}\n"
        f"severity={{{severity_fmt}}}\n"
        f"outputs={', '.join(str(path) for path in result.outputs)}\n"
        f"elapsed={math.ceil(result.elapsed_s)}s"
    )


def record_enrichment_state(result: EnrichmentResult, args: dict) -> None:
    metrics = {
        "episodes": result.episodes,
        "join_coverage": result.join_coverage,
        "non_null": result.non_null,
        "severity_hist": result.severity_hist,
        "elapsed_s": result.elapsed_s,
    }
    append_codex_state(
        "l6_enrich", args, [str(path) for path in result.outputs], metrics
    )
