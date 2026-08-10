"""
Keithley 6485 Picoammeter — current measure only (no sourcing).

Lab GPIB: primary address 15, daisy-chained with 2400-LV on the same USB-GPIB
adapter (default board=2 -> GPIB2::15::INSTR).

Manual: Model 6485 Picoammeter Instruction Manual
  - Programming example (current): *RST → ZCH ON → RANG → INIT → ZCOR →
    RANG:AUTO ON → ZCH OFF → READ?
  - Appendix B: +830 = Invalid with INFinite ARM:COUNT
  - SCPI -420 = Query UNTERMINATED (talked with nothing to send)

Discover board numbering if adapters renumber::

    python -c "import pyvisa; print(pyvisa.ResourceManager().list_resources())"
"""

from __future__ import annotations

import csv
import os
import sys
import time
from typing import List, Optional, Sequence, Tuple, Union

import pyvisa
from pymeasure.adapters import VISAAdapter

PACKAGE_DIR = os.path.abspath(os.path.dirname(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(PACKAGE_DIR, ".."))
for path in (PACKAGE_DIR, PROJECT_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

from series2400_voltage_backend import build_gpib_resource, current_epoch_ms

MODEL_ID = "6485"
DATA_FOLDER_NAME = "Keithley6485"
CSV_HEADER = ["Timestamp", "Instrument", "Measured_I"]

# Manual Table 4-1 current ranges (amps) with GUI-friendly labels.
CURRENT_RANGES: Sequence[Tuple[str, float]] = (
    ("2 nA", 2e-9),
    ("20 nA", 20e-9),
    ("200 nA", 200e-9),
    ("2 uA", 2e-6),
    ("20 uA", 20e-6),
    ("200 uA", 200e-6),
    ("2 mA", 2e-3),
    ("20 mA", 20e-3),
)
CURRENT_RANGE_LABELS = [label for label, _ in CURRENT_RANGES]
CURRENT_RANGE_BY_LABEL = {label: value for label, value in CURRENT_RANGES}

# SCPI Table 14-4: NPLC 0.01–6.0 @ 60 Hz (front-panel menu allows higher).
NPLC_SCPI_MAX = 60.0


class Keithley6485:
    """DAQ2700-style picoammeter: configure in __init__, measure in start_measure / measure_once."""

    MODEL_ID = MODEL_ID
    DATA_FOLDER_NAME = DATA_FOLDER_NAME

    def __init__(
        self,
        csv_title: Optional[str] = None,
        GPIB: str = "15",
        board: int = 2,
        resource: Optional[str] = None,
        nplc: float = 1.0,
        auto_range: bool = True,
        current_range: Optional[Union[float, str]] = None,
        zero_correct_on_connect: bool = False,
        stop_event=None,
    ):
        print(f"{self.MODEL_ID} function PID:", os.getpid())
        print(f"{self.MODEL_ID} module file:", __file__)
        print(f"Started {self.__class__.__name__}")

        visa_resource = build_gpib_resource(GPIB=GPIB, board=board, resource=resource)
        print(f"{self.MODEL_ID} opening VISA resource: {visa_resource}")

        adapter = VISAAdapter(visa_resource)
        # Thin SCPI handle; 6485 has no pymeasure instrument class.
        self.inst = adapter
        self._write = adapter.write
        self._ask = adapter.ask

        self.csv_title = os.path.abspath(csv_title) if csv_title else None
        self.stop_event = stop_event
        self.GPIB = str(GPIB).strip()
        self.board = int(board)
        self.visa_resource = visa_resource
        self.nplc = float(nplc)
        self.auto_range = bool(auto_range)
        self._apply_read_timeout()

        # *RST restores one-shot arm/trigger counts. Front-panel continuous
        # mode leaves ARM:COUN INF → READ? raises +830 and VISA times out.
        self._write("*RST")
        self._write("*CLS")
        setup_errors = self._drain_errors()
        if setup_errors:
            print(f"{self.MODEL_ID} cleared pre-setup errors:", " | ".join(setup_errors))

        self.set_zero_check(True)
        self._write("FORM:ELEM READ")
        self._force_oneshot_trigger()
        self.set_nplc(self.nplc)
        if current_range is not None:
            self.set_current_range(current_range)
            self.set_auto_range(False)
        else:
            self.set_auto_range(self.auto_range)

        if zero_correct_on_connect:
            self.perform_zero_correct()
        else:
            self.set_zero_check(False)

        post_errors = self._drain_errors()
        if post_errors:
            raise RuntimeError(
                f"{self.MODEL_ID} setup errors: " + " | ".join(post_errors)
            )

        print(
            f"{self.MODEL_ID} ready | resource={self.visa_resource} | "
            f"nplc={self.nplc} | auto_range={self.auto_range}"
        )

    # ------------------------------------------------------------------ paths
    @classmethod
    def default_csv_dir(cls) -> str:
        return os.path.join(PACKAGE_DIR, cls.DATA_FOLDER_NAME, "csv")

    @classmethod
    def default_csv_path(cls, filename: str) -> str:
        name = filename.strip()
        if not name.lower().endswith(".csv"):
            name = f"{name}.csv"
        return os.path.join(cls.default_csv_dir(), name)

    # ------------------------------------------------------------------ SCPI
    def _drain_errors(self) -> List[str]:
        errors: List[str] = []
        while True:
            err = self._ask("SYST:ERR?").strip()
            if err.startswith("0"):
                break
            errors.append(err)
        return errors

    def _apply_read_timeout(self) -> None:
        """VISA timeout (ms) sized for NPLC + auto-range headroom."""
        ms = max(3000.0, float(self.nplc) * 50.0 + 2000.0)
        self.inst.connection.timeout = ms

    def _force_oneshot_trigger(self) -> None:
        """Required before READ?. +830 if ARM:COUN is INFinite (manual App. B)."""
        self._write("ABOR")
        self._write("ARM:COUN 1")
        self._write("TRIG:COUN 1")
        self._write("TRIG:DEL 0")

    def set_nplc(self, n: float) -> None:
        """Integration rate in PLC. SCPI Table 14-4: 0.01–6.0 @ 60 Hz."""
        value = float(n)
        if value < 0.01 or value > NPLC_SCPI_MAX:
            raise ValueError(f"nplc must be in [0.01, {NPLC_SCPI_MAX:g}] for SCPI")
        self._write(f"SENS:CURR:NPLC {value:g}")
        self.nplc = value
        self._apply_read_timeout()

    def set_auto_range(self, enabled: bool) -> None:
        self.auto_range = bool(enabled)
        self._write(f"CURR:RANG:AUTO {'ON' if self.auto_range else 'OFF'}")

    def set_current_range(self, amps: Union[float, str]) -> None:
        """Manual range. Accepts amps float or label from CURRENT_RANGE_LABELS."""
        if isinstance(amps, str):
            label = amps.strip()
            if label not in CURRENT_RANGE_BY_LABEL:
                raise ValueError(
                    f"Unknown range label {label!r}; "
                    f"choose from {CURRENT_RANGE_LABELS}"
                )
            value = CURRENT_RANGE_BY_LABEL[label]
        else:
            value = float(amps)
        self._write(f"CURR:RANG {value:g}")
        self.auto_range = False

    def set_zero_check(self, enabled: bool) -> None:
        """Zero check ON = safe connect state; OFF required for real DUT current."""
        self._write(f"SYST:ZCH {'ON' if enabled else 'OFF'}")

    def perform_zero_correct(self) -> None:
        """Manual Section 3 programming example (zero-corrected current)."""
        print(f"{self.MODEL_ID}: performing zero correct...")
        self._force_oneshot_trigger()
        self._write("SYST:ZCH ON")
        self._write("CURR:RANG 2e-9")
        self._write("INIT")
        self._write("SYST:ZCOR:ACQ")
        self._write("SYST:ZCOR ON")
        if self.auto_range:
            self._write("CURR:RANG:AUTO ON")
        self._write("SYST:ZCH OFF")
        zcor_errors = self._drain_errors()
        if zcor_errors:
            raise RuntimeError(
                f"{self.MODEL_ID} zero correct errors: " + " | ".join(zcor_errors)
            )
        print(f"{self.MODEL_ID}: zero correct done (ZCOR ON, ZCH OFF)")

    def _clear_bus(self) -> None:
        try:
            self.inst.connection.clear()
        except Exception:
            pass

    def measure_once(self) -> float:
        """One-shot READ? (amps). Re-asserts finite ARM/TRIG counts every call."""
        self._force_oneshot_trigger()
        self._write("SYST:ZCH OFF")
        try:
            raw = self._ask("READ?").strip()
        except pyvisa.errors.VisaIOError as exc:
            # Surface the instrument's own reason (+830 / -420 / …).
            self._clear_bus()
            scpi_errs = self._drain_errors()
            detail = " | ".join(scpi_errs) if scpi_errs else "(no SYST:ERR? text)"
            print(f"{self.MODEL_ID} READ? TMO; SCPI errors: {detail}")
            self._force_oneshot_trigger()
            self._write("SYST:ZCH OFF")
            try:
                raw = self._ask("READ?").strip()
            except pyvisa.errors.VisaIOError:
                scpi_errs = self._drain_errors()
                detail = " | ".join(scpi_errs) if scpi_errs else str(exc)
                raise RuntimeError(
                    f"{self.MODEL_ID} READ? failed after oneshot repair: {detail}"
                ) from exc
        token = raw.split(",")[0].strip()
        return float(token)

    # ----------------------------------------------------------- solo CSV loop
    def _ensure_csv_parent(self) -> None:
        if not self.csv_title:
            raise ValueError("csv_title is required for start_measure()")
        parent = os.path.dirname(self.csv_title)
        if parent:
            os.makedirs(parent, exist_ok=True)

    def _should_stop(self) -> bool:
        return self.stop_event is not None and self.stop_event.is_set()

    def _safe_shutdown(self) -> None:
        print(f"{self.MODEL_ID} exiting.")
        try:
            self.set_zero_check(True)
        except Exception as exc:
            print(f"Error enabling zero check on shutdown:", exc)
        try:
            self.inst.close()
        except Exception as exc:
            print(f"Error during {self.MODEL_ID} close:", exc)
        print(f"{self.MODEL_ID} disconnected.")

    def start_measure(self, dwell_s: float = 0.0, max_readings: Optional[int] = None) -> None:
        """Continuous current log until stop_event / KeyboardInterrupt / max_readings."""
        self._ensure_csv_parent()
        with open(self.csv_title, "w", newline="") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(CSV_HEADER)
            print(f"{self.MODEL_ID} CSV path:", self.csv_title)
            print("Starting picoammeter acquisition...")
            count = 0
            try:
                self.set_zero_check(False)
                while True:
                    if self._should_stop():
                        print("Stop signal received. Ending 6485 acquisition.")
                        break
                    if max_readings is not None and count >= int(max_readings):
                        break
                    try:
                        current = self.measure_once()
                    except (pyvisa.errors.VisaIOError, RuntimeError) as timeout_exc:
                        print(f"Timeout during READ?: {timeout_exc}")
                        continue
                    timestamp = current_epoch_ms()
                    writer.writerow([timestamp, self.MODEL_ID, current])
                    csvfile.flush()
                    print(
                        f"{timestamp}: Instrument={self.MODEL_ID}, I={current}"
                    )
                    count += 1
                    if dwell_s and dwell_s > 0:
                        time.sleep(float(dwell_s))
            except KeyboardInterrupt:
                print("6485 acquisition stopped by user.")
            finally:
                self._safe_shutdown()


if __name__ == "__main__":
    csv_path = Keithley6485.default_csv_path("smoke_current.csv")
    print("Smoke CSV:", csv_path)
    meter = Keithley6485(
        csv_title=csv_path,
        GPIB="15",
        board=2,
        nplc=1.0,
        auto_range=True,
        zero_correct_on_connect=False,
    )
    meter.start_measure(dwell_s=0.5, max_readings=5)
