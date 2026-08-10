"""
Shared Series 2400 voltage-source backend (2410 / 2400 / 2400-LV).

SCPI used here is limited to commands documented in the Series 2400 Quick Start
Guide and User Manual (terminals, fixed V-source + I-measure, lin/log/list sweeps).
PyMeasure Keithley2400 supplies terminal helpers and basic source/measure setup;
voltage staircase/list sequences are raw SCPI wrappers around the same manuals.

GPIB / VISA resources
---------------------
Each USB-GPIB adapter is its own bus (GPIB0, GPIB1, GPIB2, ...). Daisy-chained
instruments on one adapter share that bus and are selected by primary address only.

Discover what Windows enumerated::

    python -c "import pyvisa; print(pyvisa.ResourceManager().list_resources())"

If address 24 shows up as GPIB2::24::INSTR instead of GPIB1, change only ``board=``
in the caller / ``__main__`` — keep the primary address the same.

Lab defaults (primary address / default board guess):
  2410     -> addr 1  on board 0  (daisy with DAQ2700 @ 27)
  2400     -> addr 24 on board 1
  2400-LV  -> addr 26 on board 2
"""

from __future__ import annotations

import csv
import os
import sys
import time
from typing import List, Optional, Sequence, Tuple, Union

import numpy as np
import pyvisa
from pymeasure.adapters import VISAAdapter
from pymeasure.instruments.keithley import Keithley2400

PACKAGE_DIR = os.path.abspath(os.path.dirname(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(PACKAGE_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

CSV_HEADER = ["Timestamp", "Instrument", "Setpoint_V", "Measured_V", "Measured_I"]
CSV_HEADER_WITH_6485 = CSV_HEADER + ["Measured_I_6485"]
# RPA science product: P2 sweep voltage paired with picoammeter current.
CSV_HEADER_COMBINED = ["Timestamp", "Sweep_V", "Picoammeter_I"]
LIST_SWEEP_MAX_POINTS = 100
# PyMeasure source_voltage_range validator tops out at 210 V (Model 2400).
# Model 2410 needs raw :SOUR:VOLT:RANG above that (documented Series 2400 command).
PYMEASURE_VOLT_RANGE_CEILING = 210.0


def current_epoch_ms() -> int:
    return int(time.time() * 1000)


def build_gpib_resource(
    GPIB: Union[str, int] = "1",
    board: int = 0,
    resource: Optional[str] = None,
) -> str:
    """Build a VISA GPIB INSTR resource string.

    ``resource`` wins when provided (e.g. ``GPIB1::24::INSTR``).
    Otherwise returns ``GPIB{board}::{GPIB}::INSTR``.
    """
    if resource is not None and str(resource).strip():
        return str(resource).strip()
    address = str(GPIB).strip()
    if not address:
        raise ValueError("GPIB primary address is required when resource is omitted.")
    # Allow callers to pass legacy "GPIB::24" / "GPIB0::24::INSTR" in GPIB by mistake.
    if address.upper().startswith("GPIB"):
        return address if "::INSTR" in address.upper() else f"{address}::INSTR"
    return f"GPIB{int(board)}::{address}::INSTR"


class Series2400VoltageSource:
    """DAQ2700-style lifecycle: configure in __init__, run loops in start_* methods.

    Subclasses must set class attributes:
      MODEL_ID, MAX_SOURCE_VOLTAGE, DATA_FOLDER_NAME
    """

    MODEL_ID = "Series2400"
    MAX_SOURCE_VOLTAGE = 21.0
    DATA_FOLDER_NAME = "Series2400"

    def __init__(
        self,
        csv_title: str,
        GPIB: str = "1",
        board: int = 0,
        resource: Optional[str] = None,
        terminals: str = "REAR",
        compliance_current: float = 0.01,
        voltage_range: Optional[float] = None,
        nplc: float = 1.0,
        stop_event=None,
        sense_wires: int = 2,
        companion_ammeter=None,
        role_label: Optional[str] = None,
        combined_csv=None,
    ):
        print(f"{self.MODEL_ID} function PID:", os.getpid())
        print(f"{self.MODEL_ID} module file:", __file__)
        print(f"Started {self.__class__.__name__}")

        if not csv_title or not str(csv_title).strip():
            raise ValueError("csv_title is required (full path to the CSV output file).")

        visa_resource = build_gpib_resource(GPIB=GPIB, board=board, resource=resource)
        print(f"{self.MODEL_ID} opening VISA resource: {visa_resource}")

        adapter = VISAAdapter(visa_resource)
        adapter.connection.timeout = 5e3
        smu = Keithley2400(adapter)

        smu.write("*CLS")
        setup_errors = self._drain_errors(smu)
        if setup_errors:
            print(f"{self.MODEL_ID} cleared pre-setup errors:", " | ".join(setup_errors))

        # Front/rear is exclusive; switching terminals turns OUTPUT OFF (manual).
        self.smu = smu
        self.csv_title = os.path.abspath(csv_title)
        self.stop_event = stop_event
        self.GPIB = str(GPIB).strip()
        self.board = int(board)
        self.visa_resource = visa_resource
        self.nplc = float(nplc)
        self._last_setpoint_v = 0.0
        # Duck-typed companion (e.g. Keithley6485) exposing measure_once() -> float amps.
        self.companion_ammeter = companion_ammeter
        # Optional suite sink (e.g. RPA combined CSV) with append_row(...).
        self.role_label = str(role_label).strip() if role_label else ""
        self.combined_csv = combined_csv

        self.set_terminals(terminals)
        self._configure_source_measure(compliance_current, voltage_range, sense_wires)

        post_errors = self._drain_errors(smu)
        if post_errors:
            raise RuntimeError(
                f"{self.MODEL_ID} setup errors: " + " | ".join(post_errors)
            )

        companion_note = (
            "companion=6485" if self.companion_ammeter is not None else "companion=none"
        )
        print(
            f"{self.MODEL_ID} ready | resource={self.visa_resource} | "
            f"terminals={self.get_terminals()} | "
            f"max_V={self.MAX_SOURCE_VOLTAGE} | {companion_note}"
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

    # ------------------------------------------------------------------ errors
    @staticmethod
    def _drain_errors(smu) -> List[str]:
        errors: List[str] = []
        while True:
            err = smu.ask("SYST:ERR?").strip()
            if err.startswith("0"):
                break
            errors.append(err)
        return errors

    def _validate_voltage(self, voltage: float, label: str = "voltage") -> float:
        v = float(voltage)
        if abs(v) > float(self.MAX_SOURCE_VOLTAGE):
            raise ValueError(
                f"{label}={v} V exceeds {self.MODEL_ID} max "
                f"|V|={self.MAX_SOURCE_VOLTAGE} V"
            )
        return v

    def _validate_voltage_list(self, voltages: Sequence[float]) -> List[float]:
        return [self._validate_voltage(v, "list voltage") for v in voltages]

    # ----------------------------------------------------------- configuration
    def _configure_source_measure(
        self,
        compliance_current: float,
        voltage_range: Optional[float],
        sense_wires: int,
    ) -> None:
        """Source voltage, measure current (QSG Table 1-6 / 1-8 pattern)."""
        if voltage_range is not None:
            voltage_range = self._validate_voltage(voltage_range, "voltage_range")

        if voltage_range is not None and abs(voltage_range) > PYMEASURE_VOLT_RANGE_CEILING:
            # 2410 high-V path: bypass pymeasure ±210 V property validator.
            self.smu.source_mode = "voltage"
            self.smu.write(f":SOUR:VOLT:RANG:AUTO 0;:SOUR:VOLT:RANG {voltage_range:g}")
            self.smu.compliance_current = float(compliance_current)
        else:
            self.smu.apply_voltage(
                voltage_range=voltage_range,
                compliance_current=float(compliance_current),
            )

        self.smu.measure_current(nplc=self.nplc, auto_range=True)
        # Override FORM so each READ? returns voltage + current for CSV columns.
        self.smu.write(':SENS:FUNC "CURR"')
        self.smu.write(":FORM:ELEM VOLT,CURR")
        self.smu.write(":SOUR:VOLT:MODE FIX")

        if sense_wires == 4:
            self.smu.write(":SYST:RSEN ON")
        else:
            self.smu.write(":SYST:RSEN OFF")

    def set_terminals(self, terminals: str) -> None:
        """Select FRONT or REAR only. Switching turns OUTPUT OFF (manual note)."""
        name = str(terminals).strip().upper()
        if name in ("FRONT", "FRON"):
            # PyMeasure: :ROUT:TERM FRON
            self.smu.use_front_terminals()
        elif name in ("REAR",):
            self.smu.use_rear_terminals()
        else:
            raise ValueError("terminals must be 'FRONT' or 'REAR'")
        print(
            f"{self.MODEL_ID}: terminals -> {self.get_terminals()} "
            "(output is off after terminal change)"
        )

    def get_terminals(self) -> str:
        raw = self.smu.ask(":ROUT:TERM?").strip().upper()
        if raw.startswith("FRON"):
            return "FRONT"
        if raw.startswith("REAR"):
            return "REAR"
        return raw

    def set_compliance_current(self, amps: float) -> None:
        self.smu.compliance_current = float(amps)

    def set_source_voltage_range(self, volts: float) -> None:
        """Set source range. Uses raw SCPI when |V| > 210 (2410 high-V)."""
        v = self._validate_voltage(volts, "source voltage range")
        if abs(v) > PYMEASURE_VOLT_RANGE_CEILING:
            self.smu.write(f":SOUR:VOLT:RANG:AUTO 0;:SOUR:VOLT:RANG {v:g}")
        else:
            self.smu.source_voltage_range = v

    def enable_output(self) -> None:
        self.smu.enable_source()

    def disable_output(self) -> None:
        self.smu.disable_source()

    def source_fixed(self, voltage: float) -> None:
        v = self._validate_voltage(voltage, "setpoint")
        self.smu.write(":SOUR:VOLT:MODE FIX")
        self.smu.source_voltage = v
        self._last_setpoint_v = v

    def measure_once(self) -> Tuple[float, float]:
        """Trigger one reading; return (measured_V, measured_I)."""
        raw = self.smu.ask(":READ?").strip()
        return self._parse_vi_pair(raw)

    def ramp_to(self, voltage: float, steps: int = 30, pause: float = 0.02) -> None:
        v = self._validate_voltage(voltage, "ramp target")
        self.smu.ramp_to_voltage(v, steps=int(steps), pause=float(pause))
        self._last_setpoint_v = v

    # -------------------------------------------------------------- CSV / CLI
    def _ensure_csv_parent(self) -> None:
        parent = os.path.dirname(self.csv_title)
        if parent:
            os.makedirs(parent, exist_ok=True)

    def _csv_header(self) -> List[str]:
        if self.companion_ammeter is not None:
            return list(CSV_HEADER_WITH_6485)
        return list(CSV_HEADER)

    def _read_companion_current(self):
        """Return 6485 current (float) or empty string on failure."""
        if self.companion_ammeter is None:
            return None
        if self._should_stop():
            return ""
        try:
            return float(self.companion_ammeter.measure_once())
        except Exception as exc:
            print(f"{self.MODEL_ID} companion ammeter read failed:", exc)
            return ""

    def _write_row(
        self,
        writer: csv.writer,
        csvfile,
        setpoint_v: float,
        measured_v: float,
        measured_i: float,
        read_companion: bool = True,
    ) -> None:
        timestamp = current_epoch_ms()
        row = [
            timestamp,
            self.MODEL_ID,
            setpoint_v,
            measured_v,
            measured_i,
        ]
        companion_i = None
        if self.companion_ammeter is not None:
            # Hardware staircase finishes all SMU points before this loop — a late
            # 6485 READ? is not time-aligned, and each VISA TMO blocks Stop.
            if read_companion:
                companion_i = self._read_companion_current()
            else:
                companion_i = ""
            row.append(companion_i)
        writer.writerow(row)
        csvfile.flush()
        # Combined RPA CSV is only Sweep_V + pico current (written by the sweep plate).
        if self.combined_csv is not None and self.companion_ammeter is not None:
            self.combined_csv.append_row(
                timestamp=timestamp,
                sweep_v=setpoint_v,
                pico_i="" if companion_i is None else companion_i,
            )
        if companion_i is not None:
            print(
                f"{timestamp}: Instrument={self.MODEL_ID}, "
                f"Set={setpoint_v}, V={measured_v}, I={measured_i}, "
                f"I_6485={companion_i}"
            )
        else:
            print(
                f"{timestamp}: Instrument={self.MODEL_ID}, "
                f"Set={setpoint_v}, V={measured_v}, I={measured_i}"
            )

    @staticmethod
    def _parse_vi_pair(raw: str) -> Tuple[float, float]:
        parts = [p.strip() for p in raw.split(",") if p.strip() != ""]
        if len(parts) < 2:
            raise RuntimeError(f"Expected VOLT,CURR from READ?, got: {raw!r}")
        return float(parts[0]), float(parts[1])

    @staticmethod
    def _parse_vi_stream(raw: str) -> List[Tuple[float, float]]:
        parts = [p.strip() for p in raw.split(",") if p.strip() != ""]
        if len(parts) % 2 != 0:
            raise RuntimeError(
                f"Odd number of fields in sweep READ? payload: {raw!r}"
            )
        out: List[Tuple[float, float]] = []
        for i in range(0, len(parts), 2):
            out.append((float(parts[i]), float(parts[i + 1])))
        return out

    def _should_stop(self) -> bool:
        return self.stop_event is not None and self.stop_event.is_set()

    def _safe_shutdown(self) -> None:
        print(f"{self.MODEL_ID} exiting.")
        try:
            self.smu.shutdown()
        except Exception as exc:
            print(f"Error during {self.MODEL_ID} shutdown:", exc)
            try:
                self.smu.disable_source()
            except Exception as nested:
                print(f"Error forcing OUTPUT OFF:", nested)
        print(f"{self.MODEL_ID} disconnected.")

    # --------------------------------------------------------------- run loops
    def start_fixed(self, voltage: float, dwell_s: float = 0.0) -> None:
        """Hold a fixed voltage and record current until stop_event / KeyboardInterrupt."""
        v = self._validate_voltage(voltage, "fixed voltage")
        self._ensure_csv_parent()
        with open(self.csv_title, "w", newline="") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(self._csv_header())
            print(f"{self.MODEL_ID} CSV path:", self.csv_title)
            print("Starting fixed-voltage acquisition...")
            try:
                self.source_fixed(v)
                self.enable_output()
                while True:
                    if self._should_stop():
                        print("Stop signal received. Ending fixed acquisition.")
                        break
                    try:
                        meas_v, meas_i = self.measure_once()
                    except pyvisa.errors.VisaIOError as timeout_exc:
                        print(f"Timeout during READ?: {timeout_exc}")
                        continue
                    self._write_row(writer, csvfile, v, meas_v, meas_i)
                    # if dwell_s and dwell_s > 0:
                    #     time.sleep(float(dwell_s))
            except KeyboardInterrupt:
                print("Fixed acquisition stopped by user.")
            finally:
                self._safe_shutdown()

    def start_software_sweep(
        self,
        voltages: Sequence[float],
        dwell_s: float = 1.0,
    ) -> None:
        """Host-timed step sweep: set V, wait, measure I at each point."""
        volts = self._validate_voltage_list(list(voltages))
        if not volts:
            raise ValueError("voltages list is empty")
        self._ensure_csv_parent()
        with open(self.csv_title, "w", newline="") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(self._csv_header())
            print(f"{self.MODEL_ID} CSV path:", self.csv_title)
            print("Starting software voltage sweep...")
            try:
                self.smu.write(":SOUR:VOLT:MODE FIX")
                self.enable_output()
                for v in volts:
                    if self._should_stop():
                        print("Stop signal received. Ending software sweep.")
                        break
                    self.source_fixed(v)
                    # if dwell_s and dwell_s > 0:
                    #     time.sleep(float(dwell_s))
                    try:
                        meas_v, meas_i = self.measure_once()
                    except pyvisa.errors.VisaIOError as timeout_exc:
                        print(f"Timeout during READ?: {timeout_exc}")
                        continue
                    if self._should_stop():
                        print("Stop signal received after SMU read. Ending software sweep.")
                        break
                    self._write_row(writer, csvfile, v, meas_v, meas_i)
            except KeyboardInterrupt:
                print("Software sweep stopped by user.")
            finally:
                self._safe_shutdown()

    def start_linear_sweep(
        self,
        start: float,
        stop: float,
        step: float,
        source_delay: float = 0.1,
    ) -> None:
        """Instrument linear staircase (QSG Table 1-16)."""
        start_v = self._validate_voltage(start, "start")
        stop_v = self._validate_voltage(stop, "stop")
        step_v = float(step)
        if step_v == 0:
            raise ValueError("step must be non-zero")
        # Points = (Stop - Start) / Step + 1  (manual)
        n_points = int(round(abs(stop_v - start_v) / abs(step_v))) + 1
        if n_points < 1:
            raise ValueError("linear sweep produced zero points")
        setpoints = list(np.linspace(start_v, stop_v, n_points))

        self._ensure_csv_parent()
        with open(self.csv_title, "w", newline="") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(self._csv_header())
            print(f"{self.MODEL_ID} CSV path:", self.csv_title)
            print(
                f"Starting hardware linear sweep: {start_v} -> {stop_v} "
                f"step {step_v} ({n_points} pts)"
            )
            try:
                if self._should_stop():
                    print("Stop signal received before linear sweep.")
                    return
                smu = self.smu
                smu.write(":SOUR:FUNC VOLT")
                smu.write(":SOUR:VOLT 0")
                smu.write(f":SOUR:DEL {float(source_delay):g}")
                smu.write(":SOUR:SWE:RANG BEST")
                smu.write(":SOUR:SWE:SPAC LIN")
                smu.write(f":SOUR:VOLT:STAR {start_v:g}")
                smu.write(f":SOUR:VOLT:STOP {stop_v:g}")
                smu.write(f":SOUR:VOLT:STEP {step_v:g}")
                # MODE SWE after STAR/STOP/STEP per manual note (avoid rebuild delays).
                smu.write(":SOUR:VOLT:MODE SWE")
                smu.write(f":TRIG:COUN {n_points}")
                smu.write(':SENS:FUNC "CURR"')
                smu.write(":FORM:ELEM VOLT,CURR")
                self.enable_output()
                # Lengthen timeout for long sweeps.
                smu.adapter.connection.timeout = max(30e3, n_points * 500)
                raw = smu.ask(":READ?").strip()
                pairs = self._parse_vi_stream(raw)
                for idx, (meas_v, meas_i) in enumerate(pairs):
                    if self._should_stop():
                        print("Stop signal received during linear sweep write-out.")
                        break
                    setpoint = setpoints[idx] if idx < len(setpoints) else meas_v
                    # Companion not time-aligned with finished hardware staircase.
                    self._write_row(
                        writer, csvfile, setpoint, meas_v, meas_i, read_companion=False
                    )
            except KeyboardInterrupt:
                print("Linear sweep stopped by user.")
            finally:
                try:
                    self.smu.write(":SOUR:VOLT:MODE FIX")
                except Exception:
                    pass
                self._safe_shutdown()

    def start_log_sweep(
        self,
        start: float,
        stop: float,
        points: int,
        source_delay: float = 0.1,
    ) -> None:
        """Instrument logarithmic staircase (QSG Table 1-16)."""
        start_v = self._validate_voltage(start, "start")
        stop_v = self._validate_voltage(stop, "stop")
        n_points = int(points)
        if n_points < 2 or n_points > 2500:
            raise ValueError("log sweep points must be in [2, 2500]")
        if start_v == 0 or stop_v == 0:
            raise ValueError("log sweep start/stop must be non-zero")
        if (start_v > 0) != (stop_v > 0):
            raise ValueError("log sweep start and stop must have the same sign")

        # Manual log spacing: log10 steps between start and stop.
        setpoints = list(np.geomspace(start_v, stop_v, n_points))

        self._ensure_csv_parent()
        with open(self.csv_title, "w", newline="") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(self._csv_header())
            print(f"{self.MODEL_ID} CSV path:", self.csv_title)
            print(
                f"Starting hardware log sweep: {start_v} -> {stop_v} "
                f"({n_points} pts)"
            )
            try:
                if self._should_stop():
                    print("Stop signal received before log sweep.")
                    return
                smu = self.smu
                smu.write(":SOUR:FUNC VOLT")
                smu.write(":SOUR:VOLT 0")
                smu.write(f":SOUR:DEL {float(source_delay):g}")
                smu.write(":SOUR:SWE:RANG BEST")
                smu.write(":SOUR:SWE:SPAC LOG")
                smu.write(f":SOUR:VOLT:STAR {start_v:g}")
                smu.write(f":SOUR:VOLT:STOP {stop_v:g}")
                smu.write(f":SOUR:SWE:POIN {n_points}")
                smu.write(":SOUR:VOLT:MODE SWE")
                smu.write(f":TRIG:COUN {n_points}")
                smu.write(':SENS:FUNC "CURR"')
                smu.write(":FORM:ELEM VOLT,CURR")
                self.enable_output()
                smu.adapter.connection.timeout = max(30e3, n_points * 500)
                raw = smu.ask(":READ?").strip()
                pairs = self._parse_vi_stream(raw)
                for idx, (meas_v, meas_i) in enumerate(pairs):
                    if self._should_stop():
                        print("Stop signal received during log sweep write-out.")
                        break
                    setpoint = setpoints[idx] if idx < len(setpoints) else meas_v
                    self._write_row(
                        writer, csvfile, setpoint, meas_v, meas_i, read_companion=False
                    )
            except KeyboardInterrupt:
                print("Log sweep stopped by user.")
            finally:
                try:
                    self.smu.write(":SOUR:VOLT:MODE FIX")
                except Exception:
                    pass
                self._safe_shutdown()

    def start_list_sweep(
        self,
        voltages: Sequence[float],
        source_delay: float = 0.1,
    ) -> None:
        """Instrument LIST sweep (max 100 points). Longer lists use software sweep."""
        volts = self._validate_voltage_list(list(voltages))
        if not volts:
            raise ValueError("voltages list is empty")
        if len(volts) > LIST_SWEEP_MAX_POINTS:
            print(
                f"{self.MODEL_ID}: list has {len(volts)} points "
                f"(limit {LIST_SWEEP_MAX_POINTS}); falling back to software sweep."
            )
            self.start_software_sweep(volts, dwell_s=float(source_delay))
            return

        self._ensure_csv_parent()
        with open(self.csv_title, "w", newline="") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(self._csv_header())
            print(f"{self.MODEL_ID} CSV path:", self.csv_title)
            print(f"Starting hardware list sweep ({len(volts)} pts)...")
            try:
                if self._should_stop():
                    print("Stop signal received before list sweep.")
                    return
                smu = self.smu
                list_text = ",".join(f"{v:g}" for v in volts)
                smu.write(":SOUR:FUNC VOLT")
                smu.write(":SOUR:VOLT 0")
                smu.write(f":SOUR:DEL {float(source_delay):g}")
                smu.write(":SOUR:SWE:RANG BEST")
                smu.write(":SOUR:VOLT:MODE LIST")
                smu.write(f":SOUR:LIST:VOLT {list_text}")
                smu.write(f":TRIG:COUN {len(volts)}")
                smu.write(':SENS:FUNC "CURR"')
                smu.write(":FORM:ELEM VOLT,CURR")
                self.enable_output()
                smu.adapter.connection.timeout = max(30e3, len(volts) * 500)
                raw = smu.ask(":READ?").strip()
                pairs = self._parse_vi_stream(raw)
                for idx, (meas_v, meas_i) in enumerate(pairs):
                    if self._should_stop():
                        print("Stop signal received during list sweep write-out.")
                        break
                    setpoint = volts[idx] if idx < len(volts) else meas_v
                    self._write_row(
                        writer, csvfile, setpoint, meas_v, meas_i, read_companion=False
                    )
            except KeyboardInterrupt:
                print("List sweep stopped by user.")
            finally:
                try:
                    self.smu.write(":SOUR:VOLT:MODE FIX")
                except Exception:
                    pass
                self._safe_shutdown()
