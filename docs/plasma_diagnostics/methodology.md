# Plasma Diagnostics Methodology (LP / EP / RPA)

[Back to documentation index](../README.md)

## Scope

This public package shows the LunarRego operator GUI and a reproducible I–V review
pipeline for three approved datasets only:

| Probe | Approved public CSV |
| --- | --- |
| Langmuir Probe (LP) | [examples/plasma_diagnostics/LP_07302026_140808.csv](../../examples/plasma_diagnostics/LP_07302026_140808.csv) |
| Retarding Potential Analyzer (RPA) | [examples/plasma_diagnostics/RPA_combined_07302026_172017.csv](../../examples/plasma_diagnostics/RPA_combined_07302026_172017.csv) |
| Emissive Probe (EP) | [examples/plasma_diagnostics/EP_PlasmaDiagnostics_exp.csv](../../examples/plasma_diagnostics/EP_PlasmaDiagnostics_exp.csv) |

No other LunarRego experiment logs are retained in this repository.

## Acquisition GUI

Entry point:

```powershell
python -m instrumentation.lunar_rego.GUI_LunarRego
```

The GUI follows the same operator pattern as the DAQ/TDK tools: save folder,
CSV naming, Start/Stop, and background workers. A Hardware Map assigns SMU /
panel roles for LP, EP, and each RPA plate. Only Keithley backends are runnable
in the public package; other listed instruments remain stubs.

## Plasma-potential estimates from dI/dV

Regenerate the public review plots:

```powershell
python -m instrumentation.lunar_rego.analyze_iv_curves
```

Method:

1. Sort the I–V pairs by voltage.
2. Optionally smooth noisy current (used for RPA).
3. Compute `dI/dV` with a central difference.
4. Mark `V*` at the dominant derivative feature (edge samples trimmed so a
   single end-point spike cannot steal the peak).
5. For LP, also mark floating potential `V_f` where current crosses zero.

### Latest regenerated estimates

| Diagnostic | Estimate | Notes |
| --- | --- | --- |
| LP | `V* ≈ 23.6 V`, `V_f ≈ 11.5 V` | Electron-transition peak of dI/dV; sweep still rising near 30 V |
| RPA | `V* ≈ 16.6 V` | Collector current is near the picoammeter noise floor; treat as qualitative |
| EP | `Vp ≈ 21.3 V` | High-emission floating-potential asymptote (preferred EP method) |

Review SVGs live under [docs/assets/readme/](../assets/readme/).

## Interpretation notes

- **LP:** Plasma potential is associated with the electron-transition peak of
  `dI/dV`. If the sweep does not fully enter electron saturation, `V*` is a
  lower-bound style indicator and should be read with `V_f`.
- **RPA:** The combined CSV is sweep voltage vs collector current. A clean
  retarding edge needs adequate signal-to-noise; the approved file is retained
  to show the pipeline even when the derivative feature is weak.
- **EP:** Heater/bias tables in the sheet are not themselves plasma potential.
  The floating-potential vs emission table is the public EP plasma-potential path.
