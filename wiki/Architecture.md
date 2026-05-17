# Architecture

[Full architecture document](https://github.com/harshddes/instrumentation-calibration-workbench/blob/main/docs/architecture/dataflow.md)

The project bridges two independent instrument workflows:

- TDK telemetry publishes the latest power-supply state into a JSON snapshot.
- DAQ logging reads that snapshot once per row and records freshness metadata.

This keeps the timing uncertainty visible instead of hiding it. The analysis layer can decide whether a row with `fresh`, `stale`, or `missing` TDK context should be used.
