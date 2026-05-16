"""
Dynamic inspection via `snoop` (PySnooper's modern fork).

Strategy: import the mocked twin, grab the real DAQ2700 module it loaded,
wrap `DAQ2700.DAQ2700` with @snoop, then invoke it for a few iterations.
Every variable assignment during execution is logged to `snoop.log`,
with real runtime type and value — not a static guess.

Why snoop here (vs editing DAQ2700.py): wrapping post-import keeps the
original file untouched. Any function can be traced this way.
"""

import os
import sys
import snoop

HERE = os.path.dirname(os.path.abspath(__file__))
XRAY = os.path.abspath(os.path.join(HERE, ".."))
if XRAY not in sys.path:
    sys.path.insert(0, XRAY)

from DAQ2700_mocked import import_daq2700_module, BoundedStopEvent

LOG_PATH = os.path.join(HERE, "snoop.log")

if os.path.exists(LOG_PATH):
    os.remove(LOG_PATH)

snoop.install(out=LOG_PATH, overwrite=False, color=False)

daq = import_daq2700_module()

daq.DAQ2700 = snoop(depth=1, watch_explode=("readings", "channels", "snapshot", "extracted_data"))(daq.DAQ2700)

csv_path = os.path.join(HERE, "snoop_mocked_output.csv")
stop = BoundedStopEvent(trip_after=2)

daq.DAQ2700(
    csv_title=csv_path,
    channels={"ch1": "101", "ch2": "102"},
    GPIB="27",
    stop_event=stop,
)

print(f"[snoop] wrote {LOG_PATH}")
