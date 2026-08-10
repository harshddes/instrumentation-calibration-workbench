"""
Keithley 2400-LV SourceMeter backend (voltage source, current measure).

Lab unit labeled "2400-LV" with 200 V range disabled.
Max source voltage: 21 V (SPEC-2400LV / Quick Start Guide for 2400-LV).

Lab GPIB: primary address 26 on its own USB-GPIB adapter
(default board=2 -> GPIB2::26::INSTR). Daisy-chained Keithley 6485 @ 15 on
the same board is paired as companion_ammeter so one CSV holds both readings.
  python -c "import pyvisa; print(pyvisa.ResourceManager().list_resources())"
"""

from __future__ import annotations

import os
import sys

import numpy as np

PACKAGE_DIR = os.path.abspath(os.path.dirname(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(PACKAGE_DIR, ".."))
for path in (PACKAGE_DIR, PROJECT_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

from Keithley6485 import Keithley6485
from series2400_voltage_backend import Series2400VoltageSource


class Keithley2400LV_all(Series2400VoltageSource):
    MODEL_ID = "2400-LV"
    MAX_SOURCE_VOLTAGE = 21.0
    DATA_FOLDER_NAME = "Keithley2400_LV"


if __name__ == "__main__":
    csv_path = Keithley2400LV_all.default_csv_path("smoke_software_sweep.csv")
    print("Smoke CSV:", csv_path)

    # Same GPIB bus as LV (board 2); address 15. No solo CSV — LV owns the file.
    ammeter = Keithley6485(
        csv_title=None,
        GPIB="15",
        board=2,
        nplc=1.0,
        auto_range=True,
        zero_correct_on_connect=False,
    )

    smu = Keithley2400LV_all(
        csv_title=csv_path,
        GPIB="26",
        board=2,
        terminals="REAR",
        compliance_current=0.01,
        voltage_range=20.0,
        companion_ammeter=ammeter,
    )
    voltages = np.linspace(0.0, 1.0, 3)
    try:
        smu.start_software_sweep(voltages, dwell_s=0.5)
    finally:
        try:
            ammeter.set_zero_check(True)
            ammeter.inst.close()
        except Exception as exc:
            print("6485 companion close:", exc)
