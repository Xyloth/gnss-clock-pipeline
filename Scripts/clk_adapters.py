from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable, List

from corr.clk_adapters import (
    print_adapter_metrics,
    record_adapter_state,
    run_clk_adapter,
)


def collect_input_paths(values: Iterable[str]) -> List[Path]:
    paths: List[Path] = []
    for value in values:
        if "*" in value or "?" in value or "[" in value:
            for match in Path().glob(value):
                if match.is_file():
                    paths.append(match)
        else:
            path = Path(value)
            if path.is_file():
                paths.append(path)
    return paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert RAPID/ULTRA clock files to tidy Parquet."
    )
    parser.add_argument("--source", choices=["rapid", "ultra"], required=True)
    parser.add_argument(
        "--inputs", nargs="+", required=True, help="Clock files or glob patterns."
    )
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--force", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    paths = collect_input_paths(args.inputs)
    if not paths:
        raise SystemExit("No input files matched.")

    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        default_name = "DEV_Rapid_2025" if args.source == "rapid" else "DEV_Ultra_2025"
        output_dir = Path("Reports") / default_name

    result = run_clk_adapter(args.source, paths, output_dir, force=args.force)
    print_adapter_metrics(result)
    record_adapter_state(
        result,
        {
            "source": args.source,
            "inputs": [str(p) for p in paths],
            "output_dir": str(output_dir),
            "force": args.force,
        },
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
