# Reproducibility And Public-Release Boundary

[Back to documentation index](README.md)

## Reproducible Pieces

The synthetic calibration pipeline is reproducible from files committed in this repo:

- [examples/synthetic_merged_session.csv](../examples/synthetic_merged_session.csv)
- [examples/synthetic_calibration_source.csv](../examples/synthetic_calibration_source.csv)
- [calibration_process/generate_keeper_current_calibration.py](../calibration_process/generate_keeper_current_calibration.py)
- [calibration_process/library.py](../calibration_process/library.py)

Regeneration produces JSON, CSV, and SVG artifacts under [calibration_process/artifacts/](../calibration_process/artifacts/).

## Runtime Snapshot Boundary

[tdk_snapshot.json](../tdk_snapshot.json) is retained as an approved runtime-state sample. It demonstrates the shape of the live bridge file. It is not a complete experiment archive and should not be treated as the authoritative telemetry record.

Use [examples/tdk_snapshot.example.json](../examples/tdk_snapshot.example.json) for a portable synthetic snapshot.

## Retained Support Data

The public repo intentionally keeps:

- [test_logging/](../test_logging/)
- [tdklambda/data/](../tdklambda/data/)
- [code_xray/](../code_xray/)
- [SCD_3D_AI_Lab/](../SCD_3D_AI_Lab/)
- [examples/plasma_diagnostics/](../examples/plasma_diagnostics/) — LP, EP, and RPA CSVs only

These are supporting examples for a portfolio repository. Large unrelated experiment trees and private scratch folders are not included.

## LunarRego Data Boundary

Only these three files are approved for public release:

- `LP_07302026_140808.csv`
- `RPA_combined_07302026_170652.csv`
- `EP_PlasmaDiagnostics_exp.csv`

Other LunarRego / Keithley run folders from the private lab tree are excluded on purpose.

## Vendor Documents

The TDK/VISA PDFs in [tdklambda/](../tdklambda/) are third-party reference documents. They are retained for engineering context and are not original project source code.

## Environment Notes

[requirements.txt](../requirements.txt) covers the common Python dependencies. Actual hardware operation also depends on system-level drivers and instrument connectivity, which cannot be reproduced by Python package installation alone.
