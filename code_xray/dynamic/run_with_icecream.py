"""
Dynamic inspection via `icecream` (ic).

Difference from snoop: snoop traces EVERYTHING in a function automatically;
icecream requires you to sprinkle `ic(...)` calls at the points you care about.
Trade-off: icecream is surgical and noise-free, snoop is exhaustive and noisy.

Approach: monkey-patch `str.split`, `str.strip`, and the helper functions the
DAQ calls so we can log the interesting variables at the exact moment of
definition — without editing DAQ2700.py. When that would distort semantics,
we instead wrap the public helpers (`read_snapshot`, etc.) post-import.
"""

import os
import sys
from icecream import ic

HERE = os.path.dirname(os.path.abspath(__file__))
XRAY = os.path.abspath(os.path.join(HERE, ".."))
if XRAY not in sys.path:
    sys.path.insert(0, XRAY)

LOG_PATH = os.path.join(HERE, "icecream.log")
_log_fh = open(LOG_PATH, "w", encoding="utf-8")
ic.configureOutput(prefix="ic| ", outputFunction=lambda s: _log_fh.write(s + "\n"))

from DAQ2700_mocked import import_daq2700_module, BoundedStopEvent

daq = import_daq2700_module()


def _wrap(obj, attr):
    """Replace obj.attr with a logging wrapper that ic()s args and result."""
    original = getattr(obj, attr)
    def wrapped(*args, **kwargs):
        result = original(*args, **kwargs)
        ic(attr, args, kwargs, type(result).__name__, result)
        return result
    setattr(obj, attr, wrapped)


import tdk_snapshot
_wrap(tdk_snapshot, "read_snapshot")
_wrap(tdk_snapshot, "compute_freshness")
_wrap(tdk_snapshot, "extract_tdk_columns")

daq.read_snapshot = tdk_snapshot.read_snapshot
daq.compute_freshness = tdk_snapshot.compute_freshness
daq.extract_tdk_columns = tdk_snapshot.extract_tdk_columns


_original_fake_ask = daq.Keithley2700.ask
def _ask_logged(self, cmd):
    result = _original_fake_ask(self, cmd)
    if cmd.strip() == "READ?":
        ic("READ?", type(result).__name__, result)
    return result
daq.Keithley2700.ask = _ask_logged


csv_path = os.path.join(HERE, "ic_mocked_output.csv")
stop = BoundedStopEvent(trip_after=2)

ic("pre-run", csv_path)
daq.DAQ2700(
    csv_title=csv_path,
    channels={"ch1": "101", "ch2": "102"},
    GPIB="27",
    stop_event=stop,
)
ic("post-run")

_log_fh.close()
print(f"[icecream] wrote {LOG_PATH}")
