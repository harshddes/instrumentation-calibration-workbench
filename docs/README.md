# Documentation Index

This documentation explains the public showcase version of the instrumentation and calibration workbench.

## Reading Order

1. [Repository overview](../README.md) — portfolio-facing overview.
1b. [Portfolio showcase PDF](Instrumentation_Calibration_Workbench_Showcase.pdf) — clickable multi-page PDF (`python docs/generate_showcase_pdf.py`).
2. [Architecture and data flow](architecture/dataflow.md) — DAQ, TDK, snapshot, and CSV data flow.
3. [Calibration methodology](calibration/methodology.md) — synthetic calibration artifact workflow.
4. [Plasma diagnostics methodology](plasma_diagnostics/methodology.md) — LP / EP / RPA GUI and dI/dV review.
5. [Runbooks](workflows/runbooks.md) — commands for calibration, GUIs, dashboard, and code_xray.
6. [Reproducibility](reproducibility.md) — public-release boundary, dependency notes, and data handling.

## GitHub Wiki

Companion pages (shorter summaries) are published at the [Project Wiki](https://github.com/harshddes/instrumentation-calibration-workbench/wiki). Their source files are mirrored in [../wiki/](../wiki/).

## Canonical Versus Supporting Material

The canonical technical docs are in [docs/](README.md). The retained folders [test_logging/](../test_logging/), [tdklambda/data/](../tdklambda/data/), [code_xray/](../code_xray/), and [SCD_3D_AI_Lab/](../SCD_3D_AI_Lab/) are supporting evidence and examples. Approved LunarRego CSVs live only under [examples/plasma_diagnostics/](../examples/plasma_diagnostics/). The live snapshot file [tdk_snapshot.json](../tdk_snapshot.json) is retained as an approved runtime-state sample, not as a complete experiment record.
