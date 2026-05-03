"""Corrections pipeline helpers and fixture utilities."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pandas as pd

from .benchmarks import run_benchmarks
from .cli import main as cli_main
from .clk_adapters import run_clk_adapter
from .episodes import detect_episodes
from .l6_enrich import run_enrichment
from .pipeline import aggregate_year, process_day

FIXTURES_ROOT = Path(__file__).resolve().parent.parent / "tests" / "data"


def available_fixtures() -> Iterable[str]:
    """Return fixture filenames under tests/data."""
    if not FIXTURES_ROOT.exists():
        return []
    return sorted(str(path.name) for path in FIXTURES_ROOT.iterdir() if path.is_file())


def load_fixture(name: str) -> pd.DataFrame:
    """Load a CSV or Parquet fixture into a DataFrame."""
    path = FIXTURES_ROOT / name
    if not path.exists():
        raise FileNotFoundError(f"Fixture not found: {name}")
    if path.suffix == ".csv":
        return pd.read_csv(path)
    if path.suffix in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    raise ValueError(f"Unsupported fixture type: {name}")


__all__ = [
    "available_fixtures",
    "load_fixture",
    "detect_episodes",
    "aggregate_year",
    "process_day",
    "cli_main",
    "run_clk_adapter",
    "run_benchmarks",
    "run_enrichment",
]
