# Calibration

[Full calibration methodology](https://github.com/harshddes/instrumentation-calibration-workbench/blob/main/docs/calibration/methodology.md)

The calibration package turns a raw DAQ current channel into a reference-scale value.

The public artifact is synthetic and reproducible:

```powershell
python -m calibration_process.generate_keeper_current_calibration
```

The generated JSON stores source hashes, cleaning rules, coefficients, fit quality, and artifact paths. That is the real engineering pattern: the equation is useful, but the provenance makes it trustworthy.
