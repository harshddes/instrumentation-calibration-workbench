# Calibration Methodology

## Purpose

The public calibration example demonstrates a versioned linear calibration workflow. It uses synthetic data so the full artifact lineage can be published safely.

## Current Demo Artifact

```text
calibration_process/artifacts/demo_keeper_current_linear.json
```

The generated model is:

```text
reference = slope * input + intercept
ps_1_current = 0.4 * DAQ_KEEPER_I + 0.01
```

## Source Files

```text
examples/synthetic_merged_session.csv
examples/synthetic_calibration_source.csv
calibration_process/curated_sources/synthetic_calibration_source.csv
calibration_process/raw_sources/synthetic_merged_session.csv
```

The generator copies the raw source into `raw_sources/`, computes SHA256 hashes, filters invalid rows, fits the linear model, and writes JSON/CSV/SVG artifacts.

## Regeneration

Run from the repository root:

```powershell
python -m calibration_process.generate_keeper_current_calibration
```

Expected outputs:

```text
calibration_process/artifacts/demo_keeper_current_linear.json
calibration_process/artifacts/demo_keeper_current_linear.cleaned.csv
calibration_process/artifacts/demo_keeper_current_linear.rejected.csv
calibration_process/artifacts/plots/demo_keeper_current_linear.svg
```

## Runtime Use

```python
from calibration_process import apply_keeper_current_calibration

reference_current = apply_keeper_current_calibration(1.25)
```

## Why The Artifact Matters

A coefficient pair alone is easy to copy and impossible to trust later. The JSON artifact records:

- source file paths;
- SHA256 hashes;
- input and reference columns;
- model equation;
- slope and intercept;
- sample count;
- R-squared and RMSE;
- cleaning rules;
- generated artifact paths.

That turns calibration from a notebook memory into a reusable, reviewable research object.
