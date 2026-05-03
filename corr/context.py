from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pyarrow.dataset as ds

CONTEXT_COLUMNS = [
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


@dataclass
class ContextBundle:
    df: pd.DataFrame
    available_columns: list[str]


def load_context(path: Path, systems: Sequence[str]) -> ContextBundle:
    if not path.exists():
        return ContextBundle(pd.DataFrame(), [])
    columns = {"system", "prn", "date", *CONTEXT_COLUMNS}
    dataset = ds.dataset(str(path), format="parquet")
    filter_expr = ds.field("system").isin(list(systems))
    table = dataset.to_table(
        columns=list(columns.intersection(dataset.schema.names)), filter=filter_expr
    )
    if table.num_rows == 0:
        return ContextBundle(pd.DataFrame(), [])
    df = table.to_pandas()
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    return ContextBundle(df, [col for col in CONTEXT_COLUMNS if col in df.columns])


def join_context(episodes: pd.DataFrame, bundle: ContextBundle) -> pd.DataFrame:
    if bundle.df.empty or episodes.empty:
        return episodes
    ctx = bundle.df
    key_cols = {"system", "prn", "date"}
    missing = [col for col in key_cols if col not in ctx.columns]
    if missing:
        return episodes
    ctx = ctx.copy()
    ctx["date"] = pd.to_datetime(ctx["date"]).dt.normalize()
    enriched = episodes.merge(
        ctx,
        left_on=["system", "prn", "episode_date"],
        right_on=["system", "prn", "date"],
        how="left",
        suffixes=("", "_ctx"),
    )
    return enriched.drop(columns=["date"])
