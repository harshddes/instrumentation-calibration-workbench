# Workflow Runbooks

[Back to documentation index](../README.md)

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Hardware workflows also require the relevant VISA/GPIB/serial drivers and connected instruments.

## Regenerate Calibration

```powershell
python -m calibration_process.generate_keeper_current_calibration
```

## Run DAQ GUI

```powershell
python -m instrumentation.daq.GUI_DAQ2700
```

Use `demo_logs` or another neutral output folder for public demos.

## Run TDK GUI

```powershell
python -m instrumentation.tdk.GUI_TDK
```

The GUI defaults to [tdklambda/data/](../../tdklambda/data/) for TDK logs.

## Run CSV Dashboard

```powershell
cd SCD_3D_AI_Lab
streamlit run app.py
```

The dashboard works without an API key in local heuristic mode. If model-backed behavior is desired, set `OPENAI_API_KEY` in the environment before running.

## Reproduce code_xray

```powershell
pip install pysnooper snoop icecream code2flow pyright
python code_xray\static\ast_tree.py readings
python code_xray\DAQ2700_mocked.py
python code_xray\dynamic\run_with_snoop.py
python code_xray\dynamic\run_with_icecream.py
```

[code_xray/](../../code_xray/) is retained to show how static and dynamic tracing were used to reason about DAQ variable flow.
