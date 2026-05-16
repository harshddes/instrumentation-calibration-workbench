# Reproducibility And Public-Release Boundary

## Reproducible Pieces

The synthetic calibration pipeline is reproducible from files committed in this repo:

- `examples/synthetic_merged_session.csv`
- `examples/synthetic_calibration_source.csv`
- `calibration_process/generate_keeper_current_calibration.py`
- `calibration_process/library.py`

Regeneration produces JSON, CSV, and SVG artifacts under `calibration_process/artifacts/`.

## Runtime Snapshot Boundary

`tdk_snapshot.json` is retained as an approved runtime-state sample. It demonstrates the shape of the live bridge file. It is not a complete experiment archive and should not be treated as the authoritative telemetry record.

Use `examples/tdk_snapshot.example.json` for a portable synthetic snapshot.

## Retained Support Data

The public repo intentionally keeps:

- `test_logging/`
- `tdklambda/data/`
- `code_xray/`
- `SCD_3D_AI_Lab/`

These are supporting examples for a portfolio repository. Large unrelated experiment trees and private scratch folders are not included.

## Vendor Documents

The TDK/VISA PDFs in `tdklambda/` are third-party reference documents. They are retained for engineering context and are not original project source code.

## Environment Notes

`requirements.txt` covers the common Python dependencies. Actual hardware operation also depends on system-level drivers and instrument connectivity, which cannot be reproduced by Python package installation alone.
