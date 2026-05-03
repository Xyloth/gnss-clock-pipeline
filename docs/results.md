# Results — honest writeup

## What was attempted

This pipeline was built as the data-engineering substrate for an ML experiment. Goal: predict large clock-bias spikes (severity `S2` / `S3` episodes) on the *rapid* and *ultra-rapid* IGS products, with the hope of beating the operational ~2-week latency of the *final* product.

If a model could surface high-confidence spike predictions a day or two earlier than the final product, that would be operationally useful — receivers and integrity-monitoring systems could discount affected satellites sooner.

## Approach

A binary classification target `y_big` was defined at the per-PRN per-day grain: `1` if any episode of severity `S2` or `S3` occurred, `0` otherwise. The L6 enriched feature store was the input — each row is one (system, PRN, day) observation with both clock-derived features (residual statistics, episode counts, max severity) and external context (β-angle distribution, sub-sat lat/lon, Kp, F10.7, Dst, BZ, Vsw, SymH).

Two models were evaluated:
- **Logistic regression** with one-hot encoded categorical features (system, PRN bucket) and standard-scaled numerics — as a transparent baseline.
- **Gradient-boosted trees** (`sklearn.ensemble.GradientBoostingClassifier`) over the raw numeric + categorical features.

Time-based splits: train on calendar years before the held-out year, evaluate on the held-out year. Repeated for 2022, 2023, 2024 as held-out years.

## What the evaluation showed

The models produced PR-AUC and ROC-AUC numbers above random, confirming the features have *some* signal. But:

- **Precision at any operationally useful recall (≥ 50%) was poor.** The minority class is rare (severity-2-or-higher days are < 5% of PRN-days for healthy satellites), and the model's confident-positive predictions had high false-positive rates.
- **Lead time vs. final product was effectively zero.** The features that drive the prediction — particularly the rapid-product's own residual signal — only become informative *after* the spike has begun. By the time a confident positive lands, the operational signal is already visible to the user.
- **Space-weather features helped less than hoped.** Kp / Dst / F10.7 contributed marginally but didn't drive the precision gains needed to make the predictions useful.

## Why I think it fell short

A few likely reasons, in rough order of plausibility:

1. **Signal-to-noise**: the rapid product's own clock-bias signal is dominated by ephemeris and atmospheric noise on the timescales where prediction would be useful. The "anomaly" is small relative to the day-to-day variability, even after MAD-robust detrending.
2. **Feature-target leakage at the daily grain was too coarse**: a per-day `y_big` washes out the actual spike timing, and the daily features used for prediction don't carry the sub-day timing information that would let a model anticipate (rather than confirm) the event.
3. **Insufficient sequential modeling**: the architecture treats each day as an independent observation. A sequential model (RNN / Transformer / temporal CNN) over the 30-second epoch series might capture pre-spike micro-trends. This is the most natural next thing to try.
4. **Class-imbalance handling was naive**: I used standard threshold-tuning rather than focal loss, calibrated probability cuts, or cost-sensitive training. With 5% minority class, the choice of imbalance technique matters a lot.

## What was useful regardless

The pipeline that produced the feature store *is* the durable artifact:

- 8 calendar years (2017–2024) processed
- 4 GNSS constellations (GPS, Galileo, GLONASS, BeiDou)
- 30-second cadence preserved end-to-end
- 5+ space-weather feeds joined daily
- Hive-partitioned + canonical-schema-validated outputs

That feature store would be reusable for any future modeling attempt — sequential, calibrated-probability, multi-task, or otherwise. The data-engineering work is the precondition for *any* of those experiments to be tractable.

## What I'd do differently

If I picked this up again with what I know now:

- **Move to sub-day target grain** (per-30-second-window or per-hour) so timing isn't washed out.
- **Use sequential models** (LSTM or Transformer) over the raw 30-second residual series, with the daily L3 / L6 features as static-context input.
- **Calibrate probabilities** explicitly (Platt scaling or isotonic) before threshold-tuning.
- **Run a hold-out by satellite as well as by year**: a model that learns "G05 is spiky" doesn't generalize to a future fleet upgrade.
- **Treat the IGS final product as the target, not the rapid product**, and frame the task as "predict the final-corrected residual from the rapid + space-weather features."

## Why I'm publishing the negative result

A negative result that comes with a working pipeline, an honest writeup, and clear next steps is — to me — more useful evidence of practitioner judgment than a lucky positive result on a tractable problem. I'd rather show the work than overstate it.
