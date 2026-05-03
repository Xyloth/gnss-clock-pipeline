# Data quality methodology

## Overview

Anomaly detection on satellite clocks has to navigate a noisy signal: hardware variability, propagation effects, ephemeris errors, and space-weather coupling all produce small bias deviations even when nothing is wrong. The methodology here is designed to be **robust** (single outliers shouldn't blow up the noise model) and **explicit** (every threshold is a constant in source, not a magic number buried in a notebook).

## Residual baseline

Each PRN's bias time series is detrended with a centered 31-epoch running median (~15.5 minutes at 30-second cadence). Median is preferred over mean because clock spikes are exactly the kind of outlier that pulls a moving average toward the anomaly.

```python
# corr/episodes.py
RUNNING_WINDOW = 31

def _running_median(series: pd.Series) -> pd.Series:
    return series.rolling(window=RUNNING_WINDOW, center=True, min_periods=1).median()
```

The residual signal is `bias_ns − running_median(bias_ns)`.

## Sigma estimation: MAD, not stdev

The residual scale is estimated with the median absolute deviation:

```
sigma = max(1.4826 × MAD(residuals), 0.3 ns)
```

The `1.4826` factor makes MAD a consistent estimator of the Gaussian standard deviation. The `max(..., 0.3 ns)` floor prevents `sigma → 0` on extremely quiet PRNs where division-by-zero would corrupt the z-score.

This matters because the same anomaly that we're trying to detect — a single epoch with a 5 ns spike — would inflate a sample standard deviation by roughly an order of magnitude, masking itself in the very noise model meant to detect it. MAD is unaffected.

## Severity binning

Absolute residuals are classified into four severity levels using explicit ns thresholds:

| Severity | abs(residual) range | Interpretation |
| --- | --- | --- |
| `S0` | (BLUE_THRESHOLD, 0.5) ns | Sub-threshold "blue" event — only retained when `--include-blue` |
| `S1` | [0.5, 3.0) ns | Minor deviation — typical for healthy satellites |
| `S2` | [3.0, 5.0) ns | Significant — investigate |
| `S3` | ≥ 5.0 ns | Spike — operationally meaningful |

```python
SEVERITY_BINS = np.array([0.5, 3.0, 5.0], dtype=float)
SEVERITY_LABELS = np.array(["S0", "S1", "S2", "S3"])
BLUE_THRESHOLD = 0.1
```

## Duration-based promotion

A pure-magnitude binning misses *sustained* drift. A clock that sits at 4 ns of error for 5 minutes is operationally worse than one that touches 5.5 ns for a single epoch.

The promotion rule:

```
if max_severity == "S2" and duration_s >= 120.0:
    severity = "S3"
    promoted = True
```

The `promoted` flag is preserved in the output schema so analytics downstream can distinguish "naturally S3" from "promoted from S2" episodes.

## System guard

A subtle data-quality bug is misfiled clock files: a GLONASS file accidentally placed in the GPS directory will parse cleanly (RINEX is system-agnostic), produce real-looking bias values, and silently corrupt downstream stats. `read_clk_file()` defends against this:

```python
SYSTEM_PREFIXES = {"G", "R", "E", "C", "J"}
MIN_AS_LINES = 10

# raises ClockContentError if:
#   - the AS records' PRN prefix doesn't match plan.system
#   - fewer than MIN_AS_LINES records are present
#   - epoch coverage is below the per-PRN threshold
```

The guard runs **before** any parsing into the tidy DataFrame, so misfiled inputs fail fast at the L1 boundary.

## Schema validation

`Scripts/validate_l5_v31.py` runs as a dedicated CI / deployment gate. Its checks:

1. **Canonical schema equality**: every Hive partition's Arrow schema must equal `CANONICAL_SCHEMA` exactly. Field name, type, nullability all matter.
2. **Epoch cadence**: every row's `epoch_seconds` must equal 30. Mixed-cadence data is a sign of corrupt input.
3. **Severity OR-rules**: an episode's `severity` must be one of `{S0, S1, S2, S3}`, and `max_epoch_severity` must be ≤ `severity` (you can promote, never demote).
4. **Hive ↔ per-year parity**: the Hive-partitioned dataset and the per-year flat materialization must contain the same row counts per `(system, year)`.

Failure on any of these exits non-zero — designed to be wired into a CI pipeline so corrupted refreshes don't reach downstream analytics.

## Test coverage

The `tests/` suite exercises each invariant with curated fixtures:

- `test_pipeline.py::test_blue_episode_is_preserved` — confirms `--include-blue` retains S0 episodes.
- `test_pipeline.py::test_system_guard_rejects_misfiled` — confirms the system guard raises `ClockContentError`.
- `test_normalize_validate.py` — runs both `normalize_l5_v31.py` and `validate_l5_v31.py` end-to-end against synthesized fixtures.
- `test_clk_adapters.py` — confirms the L1 tidy shape and lead-time metadata for both rapid and ultra-rapid sources.
- `test_l6_enrich.py` — confirms the L5 → L6 left-join produces ≥ 90% non-null context columns on episodes that have matching L3 days.
- `test_bench_2025.py` — confirms the rapid/ultra benchmarking pipeline produces non-empty MAE/RMSE outputs.
