# Plasma Diagnostics (LP / EP / RPA)

[Full methodology](https://github.com/harshddes/instrumentation-calibration-workbench/blob/main/docs/plasma_diagnostics/methodology.md)

## Experiment context

LunarRego studies charged lunar-regolith-simulant behavior under controlled electric fields inside a vacuum chamber. A bias electrode (visible in the chamber photo on the README) is driven positive or negative to set the desired field. Plasma diagnostics characterize the local plasma so lofting / charging observations can be interpreted against measured plasma conditions and ion-retarding structure.

## Vacuum system status (current)

The chamber vacuum train is a **roughing pump + CTI cryopump** combination.

- There is currently a **roughing-pump problem**, and the team is **troubleshooting the vacuum system**.
- Additional diagnostic runs (including fuller RPA results) are planned once vacuum is stable.
- In parallel, work is underway to **automate the automatic valve controller (AVC)** for the roughing / cryopump sequence.

## Instruments

### Langmuir Probe (LP)

A biasable electrode immersed in plasma. Sweep voltage, measure current → I–V curve.

- Floating potential `V_f`: voltage where net current ≈ 0
- Plasma-potential marker `V*`: voltage of the electron-transition peak in `dI/dV`

### Emissive Probe (EP)

A heated filament. As thermionic emission increases, floating potential approaches plasma potential from below. The public analysis uses the high-emission floating-potential asymptote as `Vp`.

### Retarding Potential Analyzer (RPA)

A multi-grid / multi-plate ion energy analyzer:

| Role | Function |
| --- | --- |
| Entrance / screening plates | Shape the admitted ion population |
| **P2 (retarding / discriminator)** | Swept voltage; ions must overcome this barrier |
| **P4 collector** | Collects transmitted ions; current measured with a Keithley 6485 picoammeter |

In this setup:

- P2 is the NPLC master and owns the lockstep sequence: set retarding voltage → read picoammeter
- P4 (2400-LV) holds collector bias while the 6485 records collector current
- Combined public CSV columns: `Timestamp, Sweep_V, Picoammeter_I`
- Non-sweep plates can hold fixed voltages and write their own CSVs; they do not free-run the 6485

Public RPA `dI/dV` review is **rudimentary only**. Do **not** call the early RPA feature voltage a plasma potential. It documents the acquisition / analysis pipeline while vacuum troubleshooting and AVC automation continue.

## Operator GUI

`python -m instrumentation.lunar_rego.GUI_LunarRego`

Tabs: LP, EP, RPA, Hardware Map. The Hardware Map assigns instrument / panel / GPIB / board per role. Only Keithley backends are runnable in the public package; Siglent / HP / Fluke remain stubs until backends exist.

## Approved public data

Only three CSVs are public:

- `LP_07302026_140808.csv`
- `RPA_combined_07302026_170652.csv`
- `EP_PlasmaDiagnostics_exp.csv`

Regenerate review plots:

```powershell
python -m instrumentation.lunar_rego.analyze_iv_curves
```
