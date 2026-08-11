# Plasma Diagnostics Methodology (LP / EP / RPA)

[Back to documentation index](../README.md)

## Experiment context

LunarRego is a vacuum-chamber campaign that couples **plasma diagnostics** to
**lunar-regolith-simulant lofting** under a controlled electric field.

Inside the chamber, a dedicated **bias electrode** (square plate on a vertical
rod in the chamber photograph) is driven to a chosen **positive or negative**
potential. That electrode sets the ambient electric field. Dust / regolith
simulant response (charging, lofting, transport) is then interpreted against
the local plasma state measured by:

| Diagnostic | Role in the campaign |
| --- | --- |
| Langmuir Probe (LP) | Local electron/ion current collection; floating and plasma-potential markers |
| Emissive Probe (EP) | Independent plasma-potential estimate via high-emission floating potential |
| Retarding Potential Analyzer (RPA) | Ion retarding / collector I–V structure (early pipeline results only) |

The public repository retains only the approved LP, EP, and RPA example CSVs
plus the operator GUI / analysis code. Other private run trees are excluded.

## Vacuum system status (current)

The chamber vacuum train uses a **roughing pump combined with a CTI cryopump**.
At the time of this public write-up:

- There is an active **roughing-pump problem**, and the team is
  **troubleshooting the vacuum system**.
- Additional diagnostic campaigns (including fuller RPA results) are planned
  once vacuum performance is restored.
- In parallel, work is underway to **automate the automatic valve controller
  (AVC)** that sequences the roughing / cryopump path.

Until the vacuum system is healthy again, treat the public RPA file as a
**rudimentary early result** that demonstrates acquisition + `dI/dV` review —
**not** as a finalized plasma-potential measurement.

## Hardware roles (RPA)

The RPA is wired as a multi-plate stack. In the LunarRego GUI, each plate is a
mapped role:

| Role | Typical function in this build |
| --- | --- |
| RPA_P0 / P1 / P3 | Fixed-bias screening / intermediate plates (backend may be stubbed) |
| **RPA_P2** | Retarding / discriminator plate — **software voltage sweep** |
| **RPA_P4** | Collector — fixed collector bias on Keithley 2400-LV |

Lockstep acquisition rule used by the GUI:

1. P2 is the **NPLC master** for the RPA suite.
2. For each sweep step: set P2 voltage → read Keithley **6485** picoammeter on
   the collector path (companion to P4).
3. Write one combined row: `Timestamp, Sweep_V, Picoammeter_I`.
4. Non-sweep plates may hold fixed voltages and write their own CSVs; they do
   **not** free-run the 6485.

Runnable backends in the public package: Keithley 2410, 2400, 2400-LV, and 6485.
Siglent / HP / Fluke entries appear in the Hardware Map but stay blocked until
a backend exists.

## Approved public CSVs

| Probe | Approved public CSV |
| --- | --- |
| Langmuir Probe (LP) | [examples/plasma_diagnostics/LP_07302026_140808.csv](../../examples/plasma_diagnostics/LP_07302026_140808.csv) |
| Retarding Potential Analyzer (RPA) | [examples/plasma_diagnostics/RPA_combined_07302026_170652.csv](../../examples/plasma_diagnostics/RPA_combined_07302026_170652.csv) |
| Emissive Probe (EP) | [examples/plasma_diagnostics/EP_PlasmaDiagnostics_exp.csv](../../examples/plasma_diagnostics/EP_PlasmaDiagnostics_exp.csv) |

## Acquisition GUI

```powershell
python -m instrumentation.lunar_rego.GUI_LunarRego
```

Operator pattern matches the DAQ/TDK tools: save folder, CSV prefix, timestamp
filenames, Start/Stop (per role and “all enabled”), background workers, and a
Hardware Map for instrument / panel / GPIB / board assignment.

Real operator screenshot: [docs/assets/readme/lunar-rego-gui.png](../assets/readme/lunar-rego-gui.png)

Chamber probe / electrode photograph: [docs/assets/readme/lunar-rego-chamber-probes.png](../assets/readme/lunar-rego-chamber-probes.png)

## Analysis: I–V and dI/dV

```powershell
python -m instrumentation.lunar_rego.analyze_iv_curves
```

Shared pipeline:

1. Load approved CSV / EP sheet blocks.
2. Sort by voltage; average duplicate voltages.
3. Optionally smooth current (RPA).
4. Compute `dI/dV` with a central difference.
5. For LP / EP, mark plasma-potential-related features as described below.
6. For RPA, report only a rudimentary collector `dI/dV` feature voltage — do
   **not** label it plasma potential in public materials.

### Physics interpretation

**Langmuir Probe**

- Ion / electron collection current vs probe bias.
- `V_f`: net current ≈ 0 (floating potential).
- `V*` from the electron-transition peak of `dI/dV` is the public plasma-potential
  marker. If the sweep has not fully entered electron saturation, treat `V*`
  together with `V_f` rather than as a final fitted sheath model.

**Emissive Probe**

- Heater / bias tables are setup curves, not plasma potential.
- As thermionic emission increases, floating potential rises toward plasma
  potential. The public estimate uses the mean of the highest-emission floating
  voltages.

**Retarding Potential Analyzer**

- Ions must overcome the retarding barrier on P2 to reach the collector.
- Collector current vs `Sweep_V` encodes the transmitted ion population.
- The public review plot shows a **rudimentary** smoothed `|dI/dV|` feature on
  the approved early sweep. That feature voltage is retained only to document
  the pipeline. **It is not reported as plasma potential.** Fuller RPA results
  are planned after vacuum troubleshooting (roughing pump + CTI cryopump) and
  AVC automation progress.

### Latest regenerated estimates

| Diagnostic | Estimate | Notes |
| --- | --- | --- |
| LP | `V* ≈ 23.6 V`, `V_f ≈ 11.5 V` | Electron-transition peak of dI/dV; sweep still rising near 30 V |
| EP | `Vp ≈ 21.3 V` | High-emission floating-potential asymptote |
| RPA | Rudimentary dI/dV feature ≈ 21.5 V | Early collector sweep only — **not** plasma potential; more runs planned |

Review SVGs and JSON summary live under [docs/assets/readme/](../assets/readme/).
