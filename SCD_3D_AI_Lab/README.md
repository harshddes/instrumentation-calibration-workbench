# 3D DAQ Data Playground

Interactive dashboard for your DAQ CSV data:

- inspect table + generated Python data-structure code
- view console-style output
- visualize data in 3D (signal space or table lattice)
- ask for code variations, run them, and inspect output
- chat with an AI coding coach (optional API key)
- play a small data-intuition game

For the repository-level engineering context and how this dashboard fits into the acquisition/calibration workflow, start with the [repository overview](../README.md) and [architecture/data-flow notes](../docs/architecture/dataflow.md).

## Quick start

```bash
cd SCD_3D_AI_Lab
python -m pip install -r requirements.txt
streamlit run app.py
```

## Optional AI key

Set an OpenAI key to enable full model-backed chat and code variation generation.

Windows PowerShell:

```powershell
$env:OPENAI_API_KEY="your_key_here"
streamlit run app.py
```

Without a key, the app runs in local heuristic mode.
