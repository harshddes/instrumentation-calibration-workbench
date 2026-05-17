# Reproducibility

[Full reproducibility notes](https://github.com/harshddes/instrumentation-calibration-workbench/blob/main/docs/reproducibility.md)

The reproducible public path is the synthetic calibration workflow. Real-time hardware operation still depends on local drivers, VISA/GPIB resources, instrument state, and physical wiring.

The retained [tdk_snapshot.json](https://github.com/harshddes/instrumentation-calibration-workbench/blob/main/tdk_snapshot.json) is a runtime-state sample, not a complete experiment archive. Use [examples/tdk_snapshot.example.json](https://github.com/harshddes/instrumentation-calibration-workbench/blob/main/examples/tdk_snapshot.example.json) when explaining the snapshot schema without relying on a live file.
