# Calibration Process

This folder keeps the reusable calibration work separate from exploratory notebook or scratchpad analysis.

Think of exploratory analysis as the scratchpad where you learn what the curve should be. Think of this folder as the calibrated tool you trust afterward.

For the full research-method write-up, artifact lineage, checksum behavior, and fit-quality interpretation, see [docs/calibration/methodology.md](../docs/calibration/methodology.md).

## Folder Roles

- [generate_keeper_current_calibration.py](generate_keeper_current_calibration.py) reads the raw CSV, cleans rows using explicit rules, fits the line, and writes the saved calibration artifact.
- [library.py](library.py) loads saved calibration artifacts and applies the equation to future measurements.
- [artifacts/](artifacts/) stores the fitted coefficients and metadata.
- [artifacts/plots/](artifacts/plots/) stores the human-review plot.

## Regenerate The Calibration

Run this from the project root:

```powershell
python -m calibration_process.generate_keeper_current_calibration
```

That updates:

- [artifacts/demo_keeper_current_linear.json](artifacts/demo_keeper_current_linear.json)
- [artifacts/plots/demo_keeper_current_linear.svg](artifacts/plots/demo_keeper_current_linear.svg)

## Use The Calibration

```python
from calibration_process import apply_keeper_current_calibration

ps1_current = apply_keeper_current_calibration(raw_daq_keeper_current)
```

The raw CSV is still valuable. It is the lab notebook. The JSON artifact is the reusable equation. The SVG plot is the sanity check for human eyes.
