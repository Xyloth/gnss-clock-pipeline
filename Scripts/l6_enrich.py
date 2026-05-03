from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable, List

from corr.l6_enrich import (
    print_enrichment_metrics,
    record_enrichment_state,
    run_enrichment,
)


def _collect(paths: Iterable[str]) -> List[Path]:
    return [Path(p) for p in paths if Path(p).exists()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Join L5 episodes with L3 daily aggregates."
    )
    parser.add_argument(
        "--episodes", nargs="+", required=True, help="Episode parquet files."
    )
    parser.add_argument("--l3", required=True, help="L3 daily parquet.")
    parser.add_argument("--output-dir", default="Reports/DEV_L6_Enriched_v31")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    episodes_paths = _collect(args.episodes)
    if not episodes_paths:
        raise SystemExit("No episode files located.")

    result = run_enrichment(
        episodes_paths=episodes_paths,
        l3_path=Path(args.l3),
        output_dir=Path(args.output_dir),
    )
    print_enrichment_metrics(result)
    record_enrichment_state(
        result,
        {
            "episodes": [str(path) for path in episodes_paths],
            "l3": args.l3,
            "output_dir": args.output_dir,
        },
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
