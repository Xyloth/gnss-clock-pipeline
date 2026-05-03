from __future__ import annotations

import argparse
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from .clk import ClockDayPlan, clk_path_for
from .pipeline import (
    RunConstraints,
    aggregate_year,
    build_context,
    log_year_state,
    write_year_output,
)

REQUIRED_COLUMNS = {
    "system",
    "prn",
    "start_utc",
    "end_utc",
    "duration_s",
    "epoch_seconds",
    "peak_ns",
    "mean_ns",
    "sigma_ns",
    "z_max",
    "severity",
    "episode_date",
}


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser("Build L5 episodes v31")
    parser.add_argument(
        "--mode", choices=["fixtures", "production"], default="production"
    )
    parser.add_argument(
        "--systems", default="G,E,R,C", help="Comma-separated system letters (e.g. G,R)"
    )
    parser.add_argument(
        "--years", default="2025", help="Year list or range (2017:2025)"
    )
    parser.add_argument(
        "--days",
        default=None,
        help="Optional comma-separated YYYY-MM-DD list to restrict processing",
    )
    parser.add_argument(
        "--include-blue", dest="include_blue", action="store_true", default=True
    )
    parser.add_argument("--no-include-blue", dest="include_blue", action="store_false")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--clk-root", default="/mnt/c/Corrections/Unzipped")
    parser.add_argument("--reports-root", default="/mnt/c/Corrections/Reports")
    parser.add_argument("--l3-path", default=None)
    parser.add_argument("--state-path", default=None)
    parser.add_argument("--max-days", type=int, default=None)
    parser.add_argument("--timeout-s", type=float, default=None)
    parser.add_argument("--max-prns", type=int, default=None)
    parser.add_argument("--min-prns", type=int, default=4)
    parser.add_argument("--min-epochs-per-prn", type=int, default=900)
    parser.add_argument("--fixtures-context", default=None)
    parser.add_argument("--fixtures-out", default="_fixtures")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.mode == "fixtures":
        return run_fixtures(args)
    return run_production(args)


def run_production(args: argparse.Namespace) -> int:
    systems = _parse_tokens(args.systems)
    years = _parse_years(args.years)
    day_filter = _parse_days(args.days)

    clk_root = Path(args.clk_root)
    reports_root = Path(args.reports_root) / "L5_Episodes_v31"
    reports_root.mkdir(parents=True, exist_ok=True)

    state_path = (
        Path(args.state_path) if args.state_path else Path(".codex/memory.jsonl")
    )
    l3_path = (
        Path(args.l3_path)
        if args.l3_path
        else Path(args.reports_root) / "L3_Final_Master"
    )
    context_bundle = build_context(l3_path, systems)

    constraints = RunConstraints(
        max_prns=args.max_prns,
        timeout_s=args.timeout_s,
        min_prns=args.min_prns,
        min_epochs_per_prn=args.min_epochs_per_prn,
    )

    years_done = 0
    total_eps = 0
    totals: Counter[str] = Counter()
    run_start = time.time()

    for system in systems:
        for year in years:
            day_plans = list(_iter_year_plans(system, year, clk_root, day_filter))
            if args.max_days is not None:
                day_plans = day_plans[: args.max_days]
            if not day_plans:
                continue

            output_path = (
                reports_root / system / f"l5_final_episodes_v31_{system}_{year}.parquet"
            )
            output_path.parent.mkdir(parents=True, exist_ok=True)

            if output_path.exists():
                if args.force:
                    output_path.unlink()
                elif args.resume and _output_is_valid(output_path):
                    continue

            year_run = aggregate_year(
                system=system,
                year=year,
                day_plans=day_plans,
                include_blue=args.include_blue,
                context_bundle=context_bundle,
                constraints=constraints,
            )

            write_year_output(output_path, year_run)
            log_year_state(state_path, year_run, include_blue=args.include_blue)

            totals.update(year_run.severity_counts)
            years_done += 1
            total_eps += len(year_run.episodes)

            print(
                f"[{system}] {year} done eps={len(year_run.episodes)} "
                f"S3={year_run.severity_counts.get('S3', 0)} "
                f"S2={year_run.severity_counts.get('S2', 0)} "
                f"S1={year_run.severity_counts.get('S1', 0)} "
                f"S0={year_run.severity_counts.get('S0', 0)}"
            )

    elapsed = (time.time() - run_start) / 60.0
    print(
        f"[TOTAL] years_done={years_done} eps={total_eps} "
        f"S3={totals.get('S3',0)} S2={totals.get('S2',0)} "
        f"S1={totals.get('S1',0)} S0={totals.get('S0',0)} elapsed={elapsed:.1f}m"
    )
    return 0


def run_fixtures(args: argparse.Namespace) -> int:
    clk_root = Path(args.clk_root)
    reports_root = Path(args.reports_root)
    fixtures_dir = reports_root / "L5_Episodes_v31" / args.fixtures_out
    fixtures_dir.mkdir(parents=True, exist_ok=True)

    state_path = (
        Path(args.state_path) if args.state_path else Path(".codex/memory.jsonl")
    )
    context_path = (
        Path(args.fixtures_context)
        if args.fixtures_context
        else (
            Path(args.l3_path)
            if args.l3_path
            else Path("tests/data/l3_fixture.parquet")
        )
    )

    systems = ["R", "G"]
    context_bundle = build_context(context_path, systems)
    constraints = RunConstraints(
        max_prns=args.max_prns,
        timeout_s=args.timeout_s,
        min_prns=args.min_prns,
        min_epochs_per_prn=args.min_epochs_per_prn,
    )

    fixture_plans: dict[tuple[str, int], list[ClockDayPlan]] = defaultdict(list)
    fixture_plans[("R", 2025)].append(
        ClockDayPlan(
            system="R",
            day=date(2025, 2, 5),
            path=clk_path_for("R", date(2025, 2, 5), clk_root),
            prn_filter=["R09"],
        )
    )
    fixture_plans[("G", 2025)].extend(
        [
            ClockDayPlan(
                system="G",
                day=date(2025, 4, 3),
                path=clk_path_for("G", date(2025, 4, 3), clk_root),
                prn_filter=["G02"],
            ),
            ClockDayPlan(
                system="G",
                day=date(2025, 1, 15),
                path=clk_path_for("G", date(2025, 1, 15), clk_root),
                prn_filter=["G03"],
            ),
        ]
    )

    run_start = time.time()
    results: dict[str, pd.DataFrame] = {}

    for (system, year), plans in fixture_plans.items():
        output_path = (
            fixtures_dir / system / f"l5_final_episodes_v31_{system}_{year}.parquet"
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if output_path.exists():
            if args.force:
                output_path.unlink()
            elif args.resume and _output_is_valid(output_path):
                results[system] = pd.read_parquet(output_path)
                continue

        year_run = aggregate_year(
            system=system,
            year=year,
            day_plans=plans,
            include_blue=True,
            context_bundle=context_bundle,
            constraints=constraints,
        )
        write_year_output(output_path, year_run)
        log_year_state(state_path, year_run, include_blue=True)
        results[system] = year_run.episodes

    r_df = results.get("R", pd.DataFrame(columns=list(REQUIRED_COLUMNS)))
    g_df = results.get("G", pd.DataFrame(columns=list(REQUIRED_COLUMNS)))

    r_metrics = _slice_metrics(r_df, system="R", prn="R09", day=date(2025, 2, 5))
    g_long_metrics = _slice_metrics(
        g_df,
        system="G",
        prn="G02",
        day=date(2025, 4, 3),
        require_duration=420,
        peak_range=(200, 250),
    )
    quiet_metrics = _slice_metrics(
        g_df, system="G", prn="G03", day=date(2025, 1, 15), expect_quiet=True
    )

    cols_union = set().union(*(df.columns for df in results.values()))
    cols_ok = REQUIRED_COLUMNS.issubset(cols_union)
    epoch_values = set()
    for df in results.values():
        if "epoch_seconds" in df.columns:
            epoch_values.update(df["epoch_seconds"].dropna().unique())

    elapsed = time.time() - run_start

    print(
        f"[CHECK] R slice: eps={r_metrics.total} S3={r_metrics.counts.get('S3',0)} "
        f"S2={r_metrics.counts.get('S2',0)} S1={r_metrics.counts.get('S1',0)} "
        f"S0={r_metrics.counts.get('S0',0)}  has_R09_S3={r_metrics.truth_ok}"
    )
    print(
        f"[CHECK] G slice: eps={g_long_metrics.total} S3={g_long_metrics.counts.get('S3',0)} "
        f"S2={g_long_metrics.counts.get('S2',0)} S1={g_long_metrics.counts.get('S1',0)} "
        f"S0={g_long_metrics.counts.get('S0',0)}  has_G02_long_S3={g_long_metrics.truth_ok}"
    )
    quiet_flag = (
        "maybe" if quiet_metrics.total == 0 else str(quiet_metrics.counts.get("S0", 0))
    )
    print(
        f"[CHECK] Quiet:   eps={quiet_metrics.total} S3={quiet_metrics.counts.get('S3',0)} "
        f"S2={quiet_metrics.counts.get('S2',0)} S1={quiet_metrics.counts.get('S1',0)} "
        f"S0={quiet_flag}"
    )
    epoch_value = next(iter(epoch_values)) if epoch_values else "NA"
    print(f"[SCHEMA] cols_ok={cols_ok} epoch_seconds={epoch_value}")
    done_msg = "<60s" if elapsed < 60 else f"{elapsed:.1f}s"
    print(f"[DONE] tests finished in {done_msg}")
    return 0


@dataclass
class SliceMetrics:
    total: int
    counts: Counter[str]
    truth_ok: bool


def _slice_metrics(
    df: pd.DataFrame,
    system: str,
    prn: str,
    day: date,
    require_duration: int | None = None,
    peak_range: tuple[float, float] | None = None,
    expect_quiet: bool = False,
) -> SliceMetrics:
    counts: Counter[str] = Counter()
    truth_ok = False
    if df.empty:
        return SliceMetrics(total=0, counts=counts, truth_ok=expect_quiet)
    mask = (
        (df["system"] == system)
        & (df["prn"] == prn)
        & (pd.to_datetime(df["episode_date"]).dt.date == day)
    )
    subset = df[mask]
    counts.update(subset["severity"].value_counts().to_dict())
    if expect_quiet:
        truth_ok = (
            subset.empty
            or not subset[subset["severity"].isin(["S1", "S2", "S3"])].any()
        )
    else:
        for _, row in subset.iterrows():
            peak = float(row["peak_ns"])
            duration = float(row["duration_s"])
            if peak_range and not (peak_range[0] <= peak <= peak_range[1]):
                continue
            if require_duration and duration < require_duration:
                continue
            if row["severity"] == "S3":
                truth_ok = True
                break
    return SliceMetrics(total=int(len(subset)), counts=counts, truth_ok=truth_ok)


def _output_is_valid(path: Path) -> bool:
    try:
        schema = pq.read_schema(path)
    except Exception:
        return False
    if not REQUIRED_COLUMNS.issubset(set(schema.names)):
        return False
    try:
        meta = pq.ParquetFile(path)
    except Exception:
        return False
    return meta.metadata is not None


def _parse_tokens(token_string: str) -> list[str]:
    return [token.strip().upper() for token in token_string.split(",") if token.strip()]


def _parse_years(spec: str) -> list[int]:
    spec = spec.strip()
    if ":" in spec:
        start, end = spec.split(":", 1)
        return list(range(int(start), int(end) + 1))
    return [int(part) for part in spec.split(",") if part]


def _parse_days(spec: str | None) -> set[date] | None:
    if not spec:
        return None
    days = set()
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        days.add(date.fromisoformat(token))
    return days


def _iter_year_plans(
    system: str,
    year: int,
    clk_root: Path,
    day_filter: set[date] | None,
) -> Iterable[ClockDayPlan]:
    current = date(year, 1, 1)
    while current.year == year:
        if day_filter is None or current in day_filter:
            yield ClockDayPlan(
                system=system,
                day=current,
                path=clk_path_for(system, current, clk_root),
            )
        current += timedelta(days=1)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
