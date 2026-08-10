# Approved Plasma-Diagnostics Examples

Public boundary: only these three CSVs are retained.

| File | Probe |
| --- | --- |
| `LP_07302026_140808.csv` | Langmuir Probe |
| `RPA_combined_07302026_170652.csv` | RPA combined collector sweep |
| `EP_PlasmaDiagnostics_exp.csv` | Emissive Probe sheet export |

Regenerate review plots:

```powershell
python -m instrumentation.lunar_rego.analyze_iv_curves
```

Methodology: [docs/plasma_diagnostics/methodology.md](../../docs/plasma_diagnostics/methodology.md)
