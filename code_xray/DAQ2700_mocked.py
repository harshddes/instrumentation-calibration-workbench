"""
Mocked twin of DAQ2700.py.

Purpose: let dynamic-analysis tools (snoop, icecream, debugger) execute the
logic end-to-end without a Keithley 2700 on GPIB or a live tdk_snapshot.json.

Strategy:
- Inject fake `pymeasure.instruments.keithley.Keithley2700` and
  `pymeasure.adapters.VISAAdapter` into sys.modules BEFORE the real DAQ2700
  module is imported. Python's import machinery then picks up the fakes.
- Stub the tdk_snapshot helpers similarly so no JSON file I/O is needed.
- Bound iteration count via threading.Event so the infinite scan loop exits.
"""

import sys
import os
import types
import time
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, ".."))


class _FakeAdapterConnection:
    def __init__(self):
        self.timeout = 0


class _FakeVISAAdapter:
    def __init__(self, resource):
        self.resource = resource
        self.connection = _FakeAdapterConnection()


class _FakeKeithley2700:
    """Minimal stand-in for pymeasure's Keithley2700.
    - `write` is a no-op (logs intent).
    - `ask` returns plausible fixtures keyed by the SCPI query.
    - `shutdown` is a no-op.
    """
    def __init__(self, adapter):
        self.adapter = adapter
        self._read_counter = 0

    def write(self, cmd):
        pass

    def ask(self, cmd):
        cmd = cmd.strip()
        if cmd == "SYST:ERR?":
            return "0,\"No error\""
        if cmd == "ROUT:SCAN?":
            return "(@101,102)"
        if cmd == "SAMP:COUN?":
            return "2"
        if cmd == "TRIG:COUN?":
            return "1"
        if cmd == "READ?":
            self._read_counter += 1
            # Two-channel scan returns two comma-separated voltages.
            return f"{0.1234 + self._read_counter * 0.001:.6f},{0.0456 + self._read_counter * 0.001:.6f}"
        return "0"

    def shutdown(self):
        pass


def _install_fake_pymeasure():
    """Inject fake pymeasure tree into sys.modules before real import."""
    pymeasure = types.ModuleType("pymeasure")
    instruments = types.ModuleType("pymeasure.instruments")
    keithley = types.ModuleType("pymeasure.instruments.keithley")
    adapters = types.ModuleType("pymeasure.adapters")

    keithley.Keithley2700 = _FakeKeithley2700
    adapters.VISAAdapter = _FakeVISAAdapter

    pymeasure.instruments = instruments
    instruments.keithley = keithley

    sys.modules["pymeasure"] = pymeasure
    sys.modules["pymeasure.instruments"] = instruments
    sys.modules["pymeasure.instruments.keithley"] = keithley
    sys.modules["pymeasure.adapters"] = adapters


def _install_fake_pyvisa():
    """DAQ2700 imports pyvisa only for its exception class. Fake it."""
    pyvisa = types.ModuleType("pyvisa")
    errors = types.ModuleType("pyvisa.errors")

    class VisaIOError(Exception):
        pass

    errors.VisaIOError = VisaIOError
    pyvisa.errors = errors

    sys.modules["pyvisa"] = pyvisa
    sys.modules["pyvisa.errors"] = errors


def _install_fake_tdk_snapshot():
    """Bypass disk I/O for tdk_snapshot helpers."""
    mod = types.ModuleType("tdk_snapshot")

    mod.MERGED_FIELDS = [
        "ps_1_voltage", "ps_1_current", "ps_1_output_state",
        "ps_2_voltage", "ps_2_current", "ps_2_output_state",
        "tdk_timestamp", "tdk_age_s", "tdk_status", "voltage_sum",
    ]

    def read_snapshot(path):
        return {
            "id": 1.0,
            "timestamp": time.time() - 0.3,
            "sequence": 1,
            "published_at": time.time() - 0.3,
            "fields": {
                "ps_1_voltage": 12.3,
                "ps_1_current": 0.56,
                "ps_1_output_state": "ON",
                "ps_2_voltage": 5.0,
                "ps_2_current": 0.22,
                "ps_2_output_state": "OFF",
            },
        }

    def compute_freshness(snapshot, daq_timestamp, max_age_s=1.2, missing_after_s=None):
        age = float(daq_timestamp) - float(snapshot["timestamp"])
        status = "fresh" if age <= max_age_s else "stale"
        return age, status

    def extract_tdk_columns(snapshot, age, status):
        out = {k: snapshot["fields"].get(k, "") for k in snapshot["fields"]}
        out["voltage_sum"] = float(out["ps_1_voltage"]) + float(out["ps_2_voltage"])
        out["tdk_timestamp"] = snapshot["timestamp"]
        out["tdk_age_s"] = age
        out["tdk_status"] = status
        return out

    mod.read_snapshot = read_snapshot
    mod.compute_freshness = compute_freshness
    mod.extract_tdk_columns = extract_tdk_columns

    sys.modules["tdk_snapshot"] = mod


def import_daq2700_module():
    """Install all fakes, then import the verbatim DAQ2700 copy from this folder."""
    _install_fake_pymeasure()
    _install_fake_pyvisa()
    _install_fake_tdk_snapshot()

    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import importlib
    if "DAQ2700" in sys.modules:
        del sys.modules["DAQ2700"]
    return importlib.import_module("DAQ2700")


class BoundedStopEvent:
    """threading.Event-like shim that returns True after N calls of is_set()."""
    def __init__(self, trip_after):
        self._n = 0
        self._trip_after = trip_after
    def is_set(self):
        self._n += 1
        return self._n > self._trip_after
    def set(self):
        self._n = self._trip_after + 1


def run(iterations=3, csv_name="mocked_output.csv"):
    daq = import_daq2700_module()
    csv_path = os.path.join(HERE, csv_name)
    stop = BoundedStopEvent(trip_after=iterations)
    daq.DAQ2700(
        csv_title=csv_path,
        channels={"ch1": "101", "ch2": "102"},
        GPIB="27",
        stop_event=stop,
    )
    return csv_path


if __name__ == "__main__":
    out = run()
    print(f"[mocked] finished, csv at: {out}")
