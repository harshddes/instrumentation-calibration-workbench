"""
Keithley 2410 SourceMeter backend (voltage source, current measure).

Max source voltage: 1100 V (Series 2400 Quick Start Guide).

Lab GPIB: primary address 1 on the USB-HS bus shared with DAQ2700 @ 27
(default board=0 -> GPIB0::1::INSTR). Verify with:
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


class Keithley2410_all(Series2400VoltageSource):
    MODEL_ID = "2410"
    MAX_SOURCE_VOLTAGE = 1100.0
    DATA_FOLDER_NAME = "Keithley2410"


if __name__ == "__main__":
    # Bench smoke: low-voltage software sweep into this instrument's csv/ folder.
    csv_path = Keithley2410_all.default_csv_path("smoke_software_sweep.csv")
    print("Smoke CSV:", csv_path)
    smu = Keithley2410_all(
        csv_title=csv_path,
        GPIB="1",
        board=0,
        terminals="REAR",
        compliance_current=0.01,
        voltage_range=20.0,
    )
    voltages = np.linspace(0.0, 1.0, 3)
    smu.start_software_sweep(voltages, dwell_s=0.5)
