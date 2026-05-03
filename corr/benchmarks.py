from __future__ import annotations

import math
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .codex_state import append_codex_state

LEAD_BINS_HOURS = list(range(0, 27, 3))


@dataclass
class BenchmarkResult:
    outputs: list[Path]
    counts_by_system: dict[str, int]
    prn_count: int
    horizons: list[str]
    top5_rmse: list[tuple[str, float]]
    elapsed_s: float


def load_tidy(paths: Iterable[Path]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in paths:
        if not path.exists():
            continue
        frames.append(pd.read_parquet(path))
    if not frames:
        return pd.DataFrame(
            columns=["system", "prn", "ts_utc", "bias_ns", "epoch_seconds"]
        )
    df = pd.concat(frames, ignore_index=True)
    df["ts_utc"] = pd.to_datetime(df["ts_utc"])
    return df


def compute_errors(
    final_df: pd.DataFrame, source_df: pd.DataFrame, source_label: str
) -> pd.DataFrame:
    if final_df.empty or source_df.empty:
        return pd.DataFrame(
            columns=[
                "source",
                "system",
                "prn",
                "day",
                "mae_ns",
                "rmse_ns",
                "medae_ns",
                "count",
            ]
        )
    merged = final_df.merge(
        source_df,
        on=["system", "prn", "ts_utc"],
        suffixes=("_final", "_src"),
        how="inner",
    )
    if merged.empty:
        return pd.DataFrame(
            columns=[
                "source",
                "system",
                "prn",
                "day",
                "mae_ns",
                "rmse_ns",
                "medae_ns",
                "count",
            ]
        )
    merged["day"] = pd.to_datetime(merged["ts_utc"]).dt.normalize()
    merged["abs_err"] = (merged["bias_ns_src"] - merged["bias_ns_final"]).abs()
    merged["sq_err"] = (merged["bias_ns_src"] - merged["bias_ns_final"]) ** 2
    merged["err"] = merged["bias_ns_src"] - merged["bias_ns_final"]

    merged["source"] = source_label
    grouped = merged.groupby(["source", "system", "prn", "day"])
    summary = grouped.agg(
        mae_ns=("abs_err", "mean"),
        rmse_ns=("sq_err", lambda s: math.sqrt(float(s.mean()))),
        medae_ns=("abs_err", "median"),
        count=("abs_err", "size"),
    )
    summary = summary.reset_index()
    return summary


def compute_lead_metrics(source_df: pd.DataFrame) -> pd.DataFrame:
    if source_df.empty or "lead_time_seconds" not in source_df.columns:
        return pd.DataFrame(
            columns=[
                "system",
                "prn",
                "day",
                "lead_bin",
                "mae_ns",
                "rmse_ns",
                "medae_ns",
                "count",
            ]
        )
    df = source_df.copy()
    if df["lead_time_seconds"].isna().all():
        return pd.DataFrame(
            columns=[
                "system",
                "prn",
                "day",
                "lead_bin",
                "mae_ns",
                "rmse_ns",
                "medae_ns",
                "count",
            ]
        )
    df = df.dropna(subset=["lead_time_seconds"])
    if df.empty:
        return pd.DataFrame(
            columns=[
                "system",
                "prn",
                "day",
                "lead_bin",
                "mae_ns",
                "rmse_ns",
                "medae_ns",
                "count",
            ]
        )
    df["lead_hours"] = df["lead_time_seconds"] / 3600.0
    bins = LEAD_BINS_HOURS
    labels = [f"{bins[i]}-{bins[i+1]}h" for i in range(len(bins) - 1)]
    lead_cat = pd.cut(df["lead_hours"], bins=bins, right=False, labels=labels)
    df["lead_bin"] = lead_cat.astype(object)
    df.loc[lead_cat.isna(), "lead_bin"] = ">=24h"
    df.loc[df["lead_hours"] >= bins[-1], "lead_bin"] = ">=24h"
    df = df.dropna(subset=["lead_bin"])
    df["lead_bin"] = df["lead_bin"].astype(str)
    if df.empty:
        return pd.DataFrame(
            columns=[
                "system",
                "prn",
                "day",
                "lead_bin",
                "mae_ns",
                "rmse_ns",
                "medae_ns",
                "count",
            ]
        )
    df["day"] = pd.to_datetime(df["ts_utc"]).dt.normalize()

    rows: list[dict[str, object]] = []
    for (system, prn, day, lead_bin), group in df.groupby(
        ["system", "prn", "day", "lead_bin"], observed=True
    ):
        if group.empty:
            continue
        if "abs_err" in group.columns:
            abs_err = group["abs_err"].astype(float)
        else:
            abs_err = (group["bias_ns_src"] - group["bias_ns_final"]).abs()
        if "sq_err" in group.columns:
            sq_err = group["sq_err"].astype(float)
        else:
            sq_err = (group["bias_ns_src"] - group["bias_ns_final"]) ** 2
        rows.append(
            {
                "system": system,
                "prn": prn,
                "day": day,
                "lead_bin": str(lead_bin),
                "mae_ns": float(abs_err.mean()),
                "rmse_ns": float(math.sqrt(float(sq_err.mean()))),
                "medae_ns": float(abs_err.median()),
                "count": int(len(group)),
            }
        )
    return pd.DataFrame(
        rows,
        columns=[
            "system",
            "prn",
            "day",
            "lead_bin",
            "mae_ns",
            "rmse_ns",
            "medae_ns",
            "count",
        ],
    )


def enrich_with_errors(
    final_df: pd.DataFrame, source_df: pd.DataFrame, source_label: str
) -> pd.DataFrame:
    merged = final_df.merge(
        source_df,
        on=["system", "prn", "ts_utc"],
        suffixes=("_final", "_src"),
        how="inner",
    )
    if merged.empty:
        return merged
    merged["abs_err"] = (merged["bias_ns_src"] - merged["bias_ns_final"]).abs()
    merged["sq_err"] = (merged["bias_ns_src"] - merged["bias_ns_final"]) ** 2
    merged["err"] = merged["bias_ns_src"] - merged["bias_ns_final"]
    merged["source"] = source_label
    return merged


def run_benchmarks(
    final_paths: Iterable[Path],
    rapid_paths: Iterable[Path],
    ultra_paths: Iterable[Path],
    output_dir: Path,
) -> BenchmarkResult:
    start = time.time()
    output_dir.mkdir(parents=True, exist_ok=True)

    final_df = load_tidy(final_paths)
    rapid_df = load_tidy(rapid_paths)
    ultra_df = load_tidy(ultra_paths)

    rapid_errors = compute_errors(final_df, rapid_df, "rapid")
    enriched_ultra = enrich_with_errors(final_df, ultra_df, "ultra")
    ultra_errors = compute_errors(final_df, ultra_df, "ultra")
    ultra_leads = compute_lead_metrics(enriched_ultra)

    outputs: list[Path] = []
    rapid_path = output_dir / "bench_rapid.parquet"
    rapid_errors.to_parquet(rapid_path, index=False)
    rapid_errors.to_csv(output_dir / "bench_rapid.csv", index=False)
    outputs.extend([rapid_path, output_dir / "bench_rapid.csv"])

    ultra_path = output_dir / "bench_ultra.parquet"
    ultra_errors.to_parquet(ultra_path, index=False)
    ultra_errors.to_csv(output_dir / "bench_ultra.csv", index=False)
    outputs.extend([ultra_path, output_dir / "bench_ultra.csv"])

    lead_path = output_dir / "bench_ultra_leads.parquet"
    ultra_leads.to_parquet(lead_path, index=False)
    outputs.append(lead_path)

    combined_errors = pd.concat([rapid_errors, ultra_errors], ignore_index=True)
    systems_counts = (
        combined_errors.groupby("system")["prn"].nunique().to_dict()
        if not combined_errors.empty
        else {}
    )
    total_prns = (
        combined_errors[["system", "prn"]].drop_duplicates().shape[0]
        if not combined_errors.empty
        else 0
    )
    horizons = (
        sorted(ultra_leads["lead_bin"].dropna().unique().tolist())
        if not ultra_leads.empty
        else []
    )

    rmse_series = combined_errors.groupby(["system", "prn"])["rmse_ns"].mean().dropna()
    top5 = rmse_series.sort_values(ascending=False).head(5)
    top5_pairs = [(prn, float(value)) for (system, prn), value in top5.items()]

    elapsed = time.time() - start
    return BenchmarkResult(
        outputs=outputs,
        counts_by_system={str(k): int(v) for k, v in systems_counts.items()},
        prn_count=int(total_prns),
        horizons=[str(h) for h in horizons],
        top5_rmse=top5_pairs,
        elapsed_s=elapsed,
    )


def print_benchmark_metrics(result: BenchmarkResult) -> None:
    counts_fmt = ", ".join(
        f"{sys}:{count}" for sys, count in sorted(result.counts_by_system.items())
    )
    horizons_fmt = ", ".join(result.horizons) if result.horizons else "none"
    top_fmt = ", ".join(f"{name}:{value:.3f}" for name, value in result.top5_rmse)
    print(
        "[BENCH_2025]\n"
        f"systems={{{counts_fmt}}} prns={result.prn_count}\n"
        f"horizons={horizons_fmt}\n"
        f"top5_rmse={top_fmt}\n"
        f"outputs={', '.join(str(p) for p in result.outputs)}\n"
        f"elapsed={math.ceil(result.elapsed_s)}s"
    )


def record_benchmark_state(result: BenchmarkResult, args: dict) -> None:
    metrics = {
        "counts_by_system": result.counts_by_system,
        "prn_count": result.prn_count,
        "horizons": result.horizons,
        "top5_rmse": result.top5_rmse,
        "elapsed_s": result.elapsed_s,
    }
    append_codex_state(
        "bench_2025", args, [str(path) for path in result.outputs], metrics
    )
