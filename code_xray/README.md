# code_xray: experiment folder for visualizing variable/data-structure flow

Target file (verbatim copy, not modified): [DAQ2700.py](DAQ2700.py).
Seed question: *"Where does `readings` come from, what is it, who touched it?"*

All tools were run against this one file so results are directly comparable.

For the repository-level architecture and the role of [DAQ2700.py](DAQ2700.py) in the acquisition pipeline, see [docs/architecture/dataflow.md](../docs/architecture/dataflow.md).

## What's in here

```text
DAQ2700.py                         verbatim copy of the real file
DAQ2700_mocked.py                  fakes Keithley + VISA + tdk_snapshot so the
                                   code runs without hardware; 3 iterations
                                   and exits cleanly. Required for dynamic tools.
static/
  ast_tree.py                      custom AST def-use walker (built from scratch)
  ast_tree.out.txt                 its output: ancestry tree + full variable index
  pyright_report.json              pyright raw JSON scan
  pyright_summary.txt              pyright human-readable summary (1 real error)
  callgraph_code2flow.gv           code2flow DOT graph (empty for this file -- see findings)
  callgraph_code2flow.json         same, as JSON
dynamic/
  run_with_snoop.py                wraps DAQ2700 with @snoop, runs mocked 3x
  snoop.log                        full line-by-line runtime trace (REAL types/values)
  run_with_icecream.py             surgical ic() wrappers on read_snapshot / ask(READ?) / etc.
  icecream.log                     only the calls you asked about, with full values
  debugger_notes.md                how to use Cursor's debugger on the mocked twin
```

## How to reproduce every output

```powershell
# (one time) install tools
pip install pysnooper snoop icecream code2flow pyright

# static
python code_xray\static\ast_tree.py readings
pyright --outputjson code_xray\DAQ2700.py > code_xray\static\pyright_report.json
pyright          code_xray\DAQ2700.py > code_xray\static\pyright_summary.txt
code2flow code_xray\DAQ2700.py -o code_xray\static\callgraph_code2flow.gv

# dynamic (no hardware needed)
python code_xray\DAQ2700_mocked.py         # sanity check
python code_xray\dynamic\run_with_snoop.py
python code_xray\dynamic\run_with_icecream.py
```

## Comparison matrix

| tool | class | setup cost | best at | what it tells you about `readings` | cost |
| --- | --- | --- | --- | --- | --- |
| **Cursor hover + Go-To-Def** | static | zero | single-variable lineage in seconds | `list[str]` from `data.split(',')`, jumps up the chain on Ctrl+Click | none |
| **Pyright / Pylance CLI** | static | one pip | whole-file type errors, unknown attrs | flags `adapter.connection.timeout` as unknown attr (real lead on line 48) | none |
| **custom AST walker** (`ast_tree.py`) | static | wrote it | custom exhaustive def-use tree | prints the full ancestry tree rooted at any name, plus every def+use of every var | n/a |
| **code2flow** | static | one pip + graphviz for PNG | call graphs of multi-function modules | **nothing useful** — DAQ2700.py is one giant function, no intra-file calls | graphviz binary for PNG |
| **Cursor debugger + breakpoints** | dynamic | one `launch.json` | "just show me the live values right now" | `readings=['0.124400','0.046600']`, `data='0.124400,0.046600'`, all simultaneously | must be able to run the code |
| **snoop** | dynamic | one pip | "record a flight log I can grep later" | full trace of every assignment and its real type/value, line-by-line | produces a lot of output |
| **icecream (`ic`)** | dynamic | one pip | "log only the 5 variables I actually care about" | just the inputs/outputs of `read_snapshot`, `compute_freshness`, `ask('READ?')` | requires surgical placement |

## Findings specific to `DAQ2700.py`

1. **The ancestry of `readings` is 5 deep**: `readings <- data <- k2700.ask <- k2700 <- adapter <- GPIB (param)`. See `static/ast_tree.out.txt` top section.
2. **Pyright caught one real static error**: `adapter.connection.timeout = 5e3` on line 48 — `ProtocolAdapter` has no `timeout` per pyright's type stubs. Either a type-stub gap in `pymeasure`, or `.connection` is not what you think it is. Worth a 30-second investigation.
3. **code2flow is the wrong tool for this file.** The file has exactly one top-level function (`DAQ2700`) and all calls are to external libraries. Call graph = empty. This tells you the file's architecture: it's a script, not a module.
4. **snoop's log shows the types you could never infer statically**, e.g. `data` is `str`, `readings` is `list[str]`, `scan_channels` is `str` = `'101,102'`, `extracted_data` is `dict` with 10 keys — confirmed, not guessed.
5. **The custom AST walker has one known wart**: attribute assignments like `adapter.connection.timeout = 5e3` log `adapter` as a redefinition. Cost of simplicity; could be fixed by restricting LHS walk to the top-level target only.

## Verdict (if you pick exactly one)

**For the question you actually asked — "where does `readings` come from and what shape is it?" — the answer is, in order of ROI:**

1. **Cursor's built-in hover + Ctrl+Click.** You already have it. Five seconds. Gives you type AND jumps you up the chain. 90% of the time this is the whole answer.
2. **A breakpoint on line 157 + the Variables panel.** Five more seconds once you've got `launch.json`. Shows real runtime types of every variable in scope at once, including shapes you cannot infer statically (dict sizes, list lengths, actual numeric values).
3. **`snoop` when you want a paper trail.** When the bug is "`readings` becomes wrong on iteration 847," the debugger is useless. Snoop's log is where you look.

**The bespoke tools (`ast_tree.py`, `pyright`, `code2flow`) are for different questions:**

- `ast_tree.py` — when you want a *visual tree* like you originally asked for, without running the code. Perfect for offline review / PR comments / scripts that can't run.
- `pyright` — when you want to find *type errors* without running.
- `code2flow` — when you want a *call graph*, and the module actually has multiple interacting functions.

**`icecream`** is the sweet spot between `print()` and `snoop`: explicit, clean, structured, but requires you to pick the call sites.

## The generalizable lesson (Gilfoyle version)

> Static tools read the recipe. Dynamic tools taste the soup.

Python's dynamic typing means *types belong to values, not to variables*. Any tool that refuses to run your code is guessing. Any tool that runs your code knows. Pick based on whether the thing you're asking about can only be answered by running — or whether a smart compiler can already tell you from the source.
