# Debugger inspection (Cursor / VS Code)

The debugger is interactive, so there is no file to "capture" — but it is
objectively the most powerful dynamic tool. Procedure against the mocked
twin in this folder:

## One-time setup (~30 seconds)

Create `.vscode/launch.json` at the repo root (or use the existing one):

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "DAQ2700 (mocked)",
      "type": "debugpy",
      "request": "launch",
      "program": "${workspaceFolder}/code_xray/DAQ2700_mocked.py",
      "console": "integratedTerminal",
      "justMyCode": false
    }
  ]
}
```

## Procedure

1. Open `code_xray/DAQ2700.py`.
2. Click the gutter to the left of **line 157** (`readings = data.split(',')`)
   to drop a breakpoint (red dot).
3. `F5` -> select "DAQ2700 (mocked)".
4. Execution pauses on line 157.

## What the Variables panel shows (real types, real values)

```
channels          : dict (len=2)       {'ch1': '101', 'ch2': '102'}
GPIB              : str                '27'
adapter           : _FakeVISAAdapter
k2700             : _FakeKeithley2700
scan_channels     : str                '101,102'
data              : str                '0.124400,0.046600'
```

Step once (F10) past line 157 and `readings` appears:

```
readings          : list (len=2)       ['0.124400', '0.046600']
```

Continue (F5) to iterate. Each loop cycle updates the panel; you can watch
`snapshot`, `extracted_data`, `merged_tdk_values` mutate in real time.

## Why this beats everything else for a single question

- Nothing to install, nothing to import, nothing to log.
- Shows EVERY variable in scope simultaneously, not just the ones you asked for.
- Right-click -> "Add to Watch" lets you pin expressions like
  `len(readings) == len(channels)` and see their truth value live.
- Hover over any symbol in the source while paused -> its live value as a tooltip.

## When it is NOT the answer

- Running a long simulation where you want a recorded trace you can grep later
  (use snoop instead).
- Reasoning about code you cannot currently run (use ast_tree + pyright).
- Drawing a call graph for a whole module (use code2flow, if the module has
  real function-to-function calls — this one does not).
