# Architecture

[Full architecture document](https://github.com/harshddes/instrumentation-calibration-workbench/blob/main/docs/architecture/dataflow.md)

The project bridges two independent instrument workflows:

- TDK telemetry publishes the latest power-supply state into a JSON snapshot.
- DAQ logging reads that snapshot once per row and records freshness metadata.

This keeps the timing uncertainty visible instead of hiding it. The analysis layer can decide whether a row with `fresh`, `stale`, or `missing` TDK context should be used.

A second path, LunarRego, maps LP / EP / RPA instrument roles through a Hardware Map and writes approved diagnostic CSVs. Those CSVs feed an I–V / `dI/dV` review used as the plasma-potential reference for the bias-electrode / regolith-lofting experiment. See [Plasma Diagnostics](PlasmaDiagnostics.md).
