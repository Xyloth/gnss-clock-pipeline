# Schema reference

## L5 canonical episode schema

Defined in `corr/l5_schema.py`. Every Hive partition under `Reports/L5_Episodes_v31_HIVE/system=<G|E|R|C>/year=<YYYY>/` is asserted to match this schema exactly.

| Field | PyArrow type | Nullable | Description |
| --- | --- | --- | --- |
| `system` | `string` | no | One of `G` (GPS), `E` (Galileo), `R` (GLONASS), `C` (BeiDou) |
| `year` | `int64` | no | Calendar year of `start_utc`, redundant for partition pruning |
| `prn` | `string` | no | Pseudo-random number / satellite ID — e.g. `G05`, `E18`, `R09`, `C24` |
| `severity` | `string` | no | Final assigned label after duration promotion: `S0`/`S1`/`S2`/`S3` |
| `max_epoch_severity` | `string` | no | Highest pure-magnitude severity seen during the episode (pre-promotion) |
| `epoch_count` | `int64` | no | Number of 30-second epochs in the episode |
| `promoted` | `bool` | no | `True` if `severity` was elevated by the duration-based promotion rule |
| `epoch_seconds` | `int64` | no | Cadence — always `30` in this dataset |
| `start_utc` | `timestamp[ns]` | no | First epoch in the episode |
| `end_utc` | `timestamp[ns]` | no | Last epoch in the episode |
| `duration_s` | `float64` | no | `(end_utc − start_utc) + epoch_seconds`; total span covered |
| `peak_ns` | `float64` | no | Max absolute residual within the episode |
| `mean_ns` | `float64` | no | Mean absolute residual within the episode |
| `sigma_ns` | `float64` | no | MAD-derived sigma estimate for the PRN's residual distribution |
| `z_max` | `float64` | no | `peak_ns / sigma_ns` |

## L1 tidy clock schema

Output of `Scripts/clk_adapters.py`. Per-source Parquet keyed off the input filename.

| Field | Type | Notes |
| --- | --- | --- |
| `system` | `string` | First letter of `prn` |
| `prn` | `string` | RINEX PRN identifier |
| `ts_utc` | `timestamp[ns]` | UTC observation time |
| `bias_ns` | `float64` | Clock bias converted from seconds → nanoseconds |
| `epoch_seconds` | `int64` | Inferred per-PRN sampling cadence (mode of timestamp diffs) |
| `source` | `string` | `rapid` \| `ultra` \| `final` |
| `issued_at` | `timestamp[ns, UTC]` | Ultra-rapid only: file publication time inferred from filename |
| `lead_time_seconds` | `float64` | Ultra-rapid only: `ts_utc − issued_at` in seconds |

## Partitioning

L5 outputs are written under two complementary layouts:

1. **Hive-partitioned** at `Reports/L5_Episodes_v31_HIVE/system=<S>/year=<Y>/part-NNNN.parquet` — designed for filter-pushdown reads in pandas / pyarrow / Spark / Snowflake external tables.
2. **Per-year flat** at `Reports/L5_Episodes_v31_YEARS/<S>/l5_final_episodes_v31_<S>_<Y>.parquet` — one file per (system, year), convenient for direct loading and used by some legacy analytics scripts.

Both layouts are produced from the same source by `Scripts/normalize_l5_v31.py`. `Scripts/validate_l5_v31.py` asserts row-count parity between them as a corruption check.

## Manifest

`normalize_l5_v31.py` writes a JSON manifest summarizing every partition produced:

```json
{
  "generated_at": "2025-04-03T12:34:56Z",
  "entries": [
    {"system": "G", "year": 2017, "rows": 142_018, "path": "system=G/year=2017/part-0000.parquet"},
    {"system": "G", "year": 2018, "rows": 0,       "path": "system=G/year=2018/part-0000.parquet"},
    ...
  ]
}
```

The manifest is intended as a downstream contract: BI tooling and audit jobs can read it without scanning the dataset, and zero-row partitions are surfaced as data-quality flags rather than silent missing rows.
