from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable, List

from corr.benchmarks import (
    print_benchmark_metrics,
    record_benchmark_state,
    run_benchmarks,
)


def _collect(paths: Iterable[str]) -> List[Path]:
    items: List[Path] = []
    for value in paths:
        path = Path(value)
        if path.exists():
            items.append(path)
    return items


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark Rapid/Ultra clocks against Final."
    )
    parser.add_argument(
        "--final", nargs="+", required=True, help="Parquet files with Final clocks."
    )
    parser.add_argument(
        "--rapid", nargs="+", required=True, help="Parquet files with Rapid clocks."
    )
    parser.add_argument(
        "--ultra", nargs="+", required=True, help="Parquet files with Ultra clocks."
    )
    parser.add_argument("--output-dir", default="Reports/DEV_Benchmarks_2025")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    final_paths = _collect(args.final)
    rapid_paths = _collect(args.rapid)
    ultra_paths = _collect(args.ultra)

    result = run_benchmarks(
        final_paths=final_paths,
        rapid_paths=rapid_paths,
        ultra_paths=ultra_paths,
        output_dir=Path(args.output_dir),
    )
    print_benchmark_metrics(result)
    record_benchmark_state(
        result,
        {
            "final": [str(p) for p in final_paths],
            "rapid": [str(p) for p in rapid_paths],
            "ultra": [str(p) for p in ultra_paths],
            "output_dir": args.output_dir,
        },
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
