"""
Keithley 2400 SourceMeter backend (voltage source, current measure).

Max source voltage: 210 V (Series 2400 Quick Start Guide).

Lab GPIB: primary address 24 on its own USB-GPIB adapter
(default board=1 -> GPIB1::24::INSTR). If list_resources() shows another
GPIBn for address 24, change only board=.
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

from series2400_voltage_backend import Series2400VoltageSource


class Keithley2400_all(Series2400VoltageSource):
    MODEL_ID = "2400"
    MAX_SOURCE_VOLTAGE = 210.0
    DATA_FOLDER_NAME = "Keithley2400"


if __name__ == "__main__":
    csv_path = Keithley2400_all.default_csv_path("smoke_software_sweep.csv")
    print("Smoke CSV:", csv_path)
    smu = Keithley2400_all(
        csv_title=csv_path,
        GPIB="24",
        board=1,
        terminals="REAR",
        compliance_current=0.01,
        voltage_range=20.0,
    )
    voltages = np.linspace(0.0, 1.0, 3)
    smu.start_software_sweep(voltages, dwell_s=0.5)
