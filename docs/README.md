# Documentation Index

This documentation explains the public showcase version of the instrumentation and calibration workbench.

## Reading Order

1. [Repository overview](../README.md) — portfolio-facing overview.
2. [Architecture and data flow](architecture/dataflow.md) — DAQ, TDK, snapshot, and CSV data flow.
3. [Calibration methodology](calibration/methodology.md) — synthetic calibration artifact workflow.
4. [Runbooks](workflows/runbooks.md) — commands for calibration, GUIs, dashboard, and code_xray.
5. [Reproducibility](reproducibility.md) — public-release boundary, dependency notes, and data handling.

## GitHub Wiki

Companion pages (shorter summaries) live in [../wiki/](../wiki/). The published site is at [Project Wiki](https://github.com/harshddes/instrumentation-calibration-workbench/wiki) once the Wiki tab has its first page; until then, use the links above.

## Canonical Versus Supporting Material

The canonical technical docs are in [docs/](README.md). The retained folders [test_logging/](../test_logging/), [tdklambda/data/](../tdklambda/data/), [code_xray/](../code_xray/), and [SCD_3D_AI_Lab/](../SCD_3D_AI_Lab/) are supporting evidence and examples. The live snapshot file [tdk_snapshot.json](../tdk_snapshot.json) is retained as an approved runtime-state sample, not as a complete experiment record.
