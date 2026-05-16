# Calibration Process

This folder keeps the reusable calibration work separate from `cali.py`.

Think of `cali.py` as the scratchpad where you explore data and learn what the curve should be. Think of this folder as the calibrated tool you trust afterward.

For the full research-method write-up, artifact lineage, checksum behavior, and fit-quality interpretation, see `../docs/calibration/keeper-current-calibration.md`.

## Folder Roles

- `generate_keeper_current_calibration.py` reads the raw CSV, cleans rows using explicit rules, fits the line, and writes the saved calibration artifact.
- `library.py` loads saved calibration artifacts and applies the equation to future measurements.
- `artifacts/` stores the fitted coefficients and metadata.
- `artifacts/plots/` stores the human-review plot.

## Regenerate The Calibration

Run this from the project root:

```powershell
python -m calibration_process.generate_keeper_current_calibration
```

That updates:

```text
calibration_process/artifacts/keeper_current_ps1_2026_04_24.json
calibration_process/artifacts/plots/keeper_current_ps1_2026_04_24.svg
```

## Use The Calibration

```python
from calibration_process import apply_keeper_current_calibration

ps1_current = apply_keeper_current_calibration(raw_daq_keeper_current)
```

The raw CSV is still valuable. It is the lab notebook. The JSON artifact is the reusable equation. The SVG plot is the sanity check for human eyes.
