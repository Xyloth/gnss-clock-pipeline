from __future__ import annotations

import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from .clk import ClockContentError, ClockDayPlan, read_clk_file
from .context import ContextBundle, join_context, load_context
from .episodes import detect_episodes
from .state import YearSummary, append_state


@dataclass
class DayRun:
    plan: ClockDayPlan
    episodes: pd.DataFrame
    skipped: bool
    reason: str | None = None


@dataclass
class YearRun:
    system: str
    year: int
    episodes: pd.DataFrame
    src_days: int
    skipped_days: int
    severity_counts: Counter
    duration_total: float
    elapsed_s: float


@dataclass
class RunConstraints:
    max_prns: int | None = None
    timeout_s: float | None = None
    min_prns: int | None = None
    min_epochs_per_prn: int | None = None


def process_day(
    plan: ClockDayPlan, include_blue: bool, constraints: RunConstraints | None = None
) -> DayRun:
    start_time = time.time()
    try:
        min_prns = (
            constraints.min_prns
            if constraints and constraints.min_prns is not None
            else 10
        )
        min_epochs = (
            constraints.min_epochs_per_prn
            if constraints and constraints.min_epochs_per_prn is not None
            else 2000
        )
        records = read_clk_file(plan, min_prns=min_prns, min_epochs_per_prn=min_epochs)
    except FileNotFoundError:
        return DayRun(
            plan=plan, episodes=pd.DataFrame(), skipped=True, reason="missing"
        )
    except ClockContentError as err:
        return DayRun(plan=plan, episodes=pd.DataFrame(), skipped=True, reason=str(err))

    episode_rows: list[dict[str, object]] = []
    grouped = sorted(records.groupby("prn"), key=lambda item: item[0])
    if constraints and constraints.max_prns:
        grouped = grouped[: constraints.max_prns]
    for _prn, group in grouped:
        if constraints and constraints.timeout_s:
            if time.time() - start_time > constraints.timeout_s:
                return DayRun(
                    plan=plan,
                    episodes=pd.DataFrame(episode_rows),
                    skipped=True,
                    reason="timeout",
                )
        episodes = detect_episodes(group, include_blue=include_blue)
        for episode in episodes:
            episode_rows.append(episode.to_dict())
    if episode_rows:
        df = pd.DataFrame(episode_rows)
    else:
        df = pd.DataFrame(
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
                "epoch_seconds",
                "severity",
                "episode_date",
            ]
        )
    return DayRun(plan=plan, episodes=df, skipped=False)


def aggregate_year(
    system: str,
    year: int,
    day_plans: Sequence[ClockDayPlan],
    include_blue: bool,
    context_bundle: ContextBundle,
    constraints: RunConstraints | None = None,
) -> YearRun:
    start_time = time.time()
    all_rows: list[pd.DataFrame] = []
    src_days = 0
    skipped_days = 0
    severity_counts: Counter[str] = Counter()
    duration_total = 0.0
    for plan in day_plans:
        result = process_day(
            plan,
            include_blue=include_blue,
            constraints=constraints,
        )
        if result.skipped:
            skipped_days += 1
            continue
        src_days += 1
        if not result.episodes.empty:
            all_rows.append(result.episodes)
            severity_counts.update(result.episodes["severity"].value_counts().to_dict())
            duration_total += float(result.episodes["duration_s"].sum())
    if all_rows:
        year_df = pd.concat(all_rows, ignore_index=True)
    else:
        year_df = pd.DataFrame(
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
                "epoch_seconds",
                "severity",
                "episode_date",
            ]
        )
    year_df["system"] = system
    year_df["year"] = year
    year_df["episode_date"] = pd.to_datetime(year_df["episode_date"]).dt.normalize()
    year_df = join_context(year_df, context_bundle)
    elapsed = time.time() - start_time
    return YearRun(
        system=system,
        year=year,
        episodes=year_df,
        src_days=src_days,
        skipped_days=skipped_days,
        severity_counts=severity_counts,
        duration_total=duration_total,
        elapsed_s=elapsed,
    )


def write_year_output(path: Path, year_run: YearRun) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    if temp_path.exists():
        temp_path.unlink()
    year_run.episodes.to_parquet(temp_path, index=False)
    temp_path.replace(path)


def log_year_state(state_path: Path, year_run: YearRun, include_blue: bool) -> None:
    summary = YearSummary(
        ts=datetime.utcnow(),
        system=year_run.system,
        year=year_run.year,
        eps_total=int(len(year_run.episodes)),
        s3=int(year_run.severity_counts.get("S3", 0)),
        s2=int(year_run.severity_counts.get("S2", 0)),
        s1=int(year_run.severity_counts.get("S1", 0)),
        s0=int(year_run.severity_counts.get("S0", 0)),
        blue_included=include_blue,
        src_days=year_run.src_days,
        skipped_days=year_run.skipped_days,
        duration_s=float(year_run.duration_total),
    )
    append_state(state_path, summary)


def build_context(path: Path, systems: Sequence[str]) -> ContextBundle:
    return load_context(path, systems)
