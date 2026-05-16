import datetime
import os
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Callable, Dict, List, Optional

from instrumentation.tdk.TDKLogic import (
    HV_DEFAULT_FALLBACK_LIMITS,
    HV_FALLBACK_LIMITS_BY_ADDRESS,
    TDKLambda,
    run_logging_session,
)


class TDKGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("TDK Lambda GUI")

        self.script_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "tdklambda", "data")
        )
        self.folder_var = tk.StringVar(value=self.script_dir)
        self.csv_name_var = tk.StringVar(value="")
        self.use_timestamp = tk.BooleanVar(value=False)

        self.port_var = tk.StringVar(value="ASRL4::INSTR")
        self.sample_period_var = tk.StringVar(value="0.1")

        self.mode_var = tk.StringVar(value="Auto")
        self.ps1_voltage_var = tk.StringVar()
        self.ps1_current_var = tk.StringVar()
        self.ps2_voltage_var = tk.StringVar()
        self.ps2_current_var = tk.StringVar()
        self.hv_mode_var = tk.BooleanVar(value=False)
        self.hv_arm_var = tk.BooleanVar(value=False)
        self.hv_indicator_var = tk.StringVar(value="")

        self.status_var = tk.StringVar(value="Idle.")
        self.stop_flag = threading.Event()
        self.scan_thread: Optional[threading.Thread] = None
        self.output_automation_thread = None
        self.control_threads: List[threading.Thread] = []
        self.manual_entries: List[tk.Entry] = []
        self.hv_arm_checkbutton: Optional[tk.Checkbutton] = None
        self.tdk_lock = threading.Lock()
        self.control_request_event = threading.Event()
        self.control_timing_lock = threading.Lock()
        self.control_timing: Dict[str, object] = {
            "marker_id": 0,
            "seen_marker_id": 0,
            "dispatch_ts": None,
            "first_measure_ts": None,
            "delta_ms": None,
            "label": "",
            "awaiting_measurement": False,
        }
        self.shared_tdk = None
        self.shared_tdk_port = ""

        self.build_widgets()
        self.toggle_csv_entry()
        self.update_manual_entries_state()
        self.update_hv_controls_state()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def build_widgets(self) -> None:
        self.root.columnconfigure(1, weight=1)

        tk.Label(self.root, text="Save Folder:").grid(row=0, column=0, padx=4, pady=4, sticky="w")
        tk.Entry(self.root, textvariable=self.folder_var, width=50).grid(
            row=0, column=1, padx=4, pady=4, sticky="we"
        )
        tk.Button(self.root, text="Choose...", command=self.choose_folder).grid(
            row=0, column=2, padx=4, pady=4
        )

        tk.Label(self.root, text="CSV File Name:").grid(row=1, column=0, padx=4, pady=4, sticky="w")
        self.csv_entry = tk.Entry(self.root, textvariable=self.csv_name_var)
        self.csv_entry.grid(row=1, column=1, padx=4, pady=4, sticky="we")
        tk.Checkbutton(
            self.root,
            text="Use timestamped filename",
            variable=self.use_timestamp,
            command=self.toggle_csv_entry,
        ).grid(row=1, column=2, padx=4, pady=4, sticky="w")

        tk.Label(self.root, text="Port:").grid(row=2, column=0, padx=4, pady=4, sticky="w")
        tk.Entry(self.root, textvariable=self.port_var).grid(row=2, column=1, padx=4, pady=4, sticky="we")

        tk.Label(self.root, text="Sample s:").grid(row=3, column=0, padx=4, pady=4, sticky="w")
        tk.Entry(self.root, textvariable=self.sample_period_var, width=10).grid(
            row=3, column=1, padx=4, pady=4, sticky="w"
        )

        tk.Label(self.root, text="Mode:").grid(row=4, column=0, padx=4, pady=4, sticky="w")
        tk.OptionMenu(self.root, self.mode_var, "Auto", "Manual", command=self._on_mode_changed).grid(
            row=4, column=1, padx=4, pady=4, sticky="w"
        )

        hv_frame = tk.Frame(self.root)
        hv_frame.grid(row=5, column=0, columnspan=3, padx=4, pady=4, sticky="we")
        tk.Checkbutton(
            hv_frame,
            text="HV Mode",
            variable=self.hv_mode_var,
            command=self._on_hv_mode_changed,
        ).grid(row=0, column=0, padx=4, pady=2, sticky="w")
        self.hv_arm_checkbutton = tk.Checkbutton(
            hv_frame,
            text="ARM HV",
            variable=self.hv_arm_var,
            command=self._on_hv_arm_changed,
        )
        self.hv_arm_checkbutton.grid(row=0, column=1, padx=4, pady=2, sticky="w")
        tk.Label(
            hv_frame,
            textvariable=self.hv_indicator_var,
            fg="red",
            anchor="w",
        ).grid(row=0, column=2, padx=8, pady=2, sticky="w")

        manual_frame = tk.LabelFrame(self.root, text="Manual Setters (optional per field)")
        manual_frame.grid(row=6, column=0, columnspan=3, padx=4, pady=6, sticky="we")
        manual_frame.columnconfigure(1, weight=1)
        manual_frame.columnconfigure(3, weight=1)

        tk.Label(manual_frame, text="PS1 Voltage:").grid(row=0, column=0, padx=4, pady=4, sticky="w")
        ps1_voltage_entry = tk.Entry(manual_frame, textvariable=self.ps1_voltage_var, width=14)
        ps1_voltage_entry.grid(row=0, column=1, padx=4, pady=4, sticky="w")

        tk.Label(manual_frame, text="PS1 Current:").grid(row=0, column=2, padx=4, pady=4, sticky="w")
        ps1_current_entry = tk.Entry(manual_frame, textvariable=self.ps1_current_var, width=14)
        ps1_current_entry.grid(row=0, column=3, padx=4, pady=4, sticky="w")

        tk.Label(manual_frame, text="PS2 Voltage:").grid(row=1, column=0, padx=4, pady=4, sticky="w")
        ps2_voltage_entry = tk.Entry(manual_frame, textvariable=self.ps2_voltage_var, width=14)
        ps2_voltage_entry.grid(row=1, column=1, padx=4, pady=4, sticky="w")

        tk.Label(manual_frame, text="PS2 Current:").grid(row=1, column=2, padx=4, pady=4, sticky="w")
        ps2_current_entry = tk.Entry(manual_frame, textvariable=self.ps2_current_var, width=14)
        ps2_current_entry.grid(row=1, column=3, padx=4, pady=4, sticky="w")

        self.manual_entries = [
            ps1_voltage_entry,
            ps1_current_entry,
            ps2_voltage_entry,
            ps2_current_entry,
        ]

        control_frame = tk.Frame(self.root)
        control_frame.grid(row=7, column=0, columnspan=3, padx=4, pady=6, sticky="we")
        tk.Button(control_frame, text="Global TURN ON", command=self.handle_global_on).grid(
            row=0, column=0, padx=4, pady=4
        )
        tk.Button(control_frame, text="Global TURN OFF", command=self.handle_global_off).grid(
            row=0, column=1, padx=4, pady=4
        )
        tk.Button(control_frame, text="PS1 TURN ON", command=lambda: self.handle_ps_on(1)).grid(
            row=0, column=2, padx=4, pady=4
        )
        tk.Button(control_frame, text="PS1 TURN OFF", command=lambda: self.handle_ps_off(1)).grid(
            row=0, column=3, padx=4, pady=4
        )
        tk.Button(control_frame, text="PS2 TURN ON", command=lambda: self.handle_ps_on(2)).grid(
            row=0, column=4, padx=4, pady=4
        )
        tk.Button(control_frame, text="PS2 TURN OFF", command=lambda: self.handle_ps_off(2)).grid(
            row=0, column=5, padx=4, pady=4
        )

        tk.Button(self.root, text="Start", command=self.start_scan).grid(row=8, column=0, padx=4, pady=6)
        tk.Button(self.root, text="Stop", command=self.stop_scan).grid(row=8, column=1, padx=4, pady=6, sticky="w")
        tk.Label(self.root, textvariable=self.status_var, anchor="w").grid(
            row=9, column=0, columnspan=3, padx=4, pady=4, sticky="we"
        )

    def _on_mode_changed(self, _value: str) -> None:
        self.update_manual_entries_state()

    def toggle_csv_entry(self) -> None:
        self.csv_entry.configure(state="disabled" if self.use_timestamp.get() else "normal")

    def update_manual_entries_state(self) -> None:
        state = "normal" if self.mode_var.get() == "Manual" else "disabled"
        for entry in self.manual_entries:
            entry.configure(state=state)

    def _on_hv_mode_changed(self) -> None:
        if not self.hv_mode_var.get():
            self.hv_arm_var.set(False)
        self.update_hv_controls_state()

    def _on_hv_arm_changed(self) -> None:
        self.update_hv_controls_state()

    def update_hv_controls_state(self) -> None:
        hv_enabled = self.hv_mode_var.get()
        if self.hv_arm_checkbutton is not None:
            self.hv_arm_checkbutton.configure(state="normal" if hv_enabled else "disabled")

        if hv_enabled and self.hv_arm_var.get():
            self.hv_indicator_var.set("⚡ HV MODE ARMED")
        elif hv_enabled:
            self.hv_indicator_var.set("⚡ HV MODE (NOT ARMED)")
        else:
            self.hv_indicator_var.set("")

    def _is_hv_armed_for_on(self, action_label: str) -> bool:
        if self.hv_mode_var.get() and not self.hv_arm_var.get():
            self._set_status(f"{action_label} blocked: enable ARM HV first.")
            return False
        return True

    def _consume_hv_arm(self) -> None:
        if self.hv_mode_var.get():
            self.hv_arm_var.set(False)
            self.update_hv_controls_state()

    def choose_folder(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.script_dir)
        if folder:
            self.folder_var.set(folder)

    def _set_status(self, text: str) -> None:
        self.root.after(0, lambda: self.status_var.set(text))

    def _get_shared_tdk(self) -> TDKLambda:
        """Return one shared TDK session for scan and control threads."""
        port = self.port_var.get().strip()
        if not port:
            raise ValueError("Port is required.")

        with self.tdk_lock:
            if self.shared_tdk is not None and self.shared_tdk_port != port:
                if self.scan_thread is not None and self.scan_thread.is_alive():
                    raise RuntimeError("Stop logging before changing the port.")
                try:
                    self.shared_tdk.close()
                except Exception:
                    pass
                self.shared_tdk = None
                self.shared_tdk_port = ""

            if self.shared_tdk is None:
                self.shared_tdk = TDKLambda(port=port, address=None)
                self.shared_tdk_port = port

            return self.shared_tdk

    def _close_shared_tdk(self) -> None:
        with self.tdk_lock:
            if self.shared_tdk is not None:
                try:
                    self.shared_tdk.close()
                except Exception:
                    pass
            self.shared_tdk = None
            self.shared_tdk_port = ""

    def _show_error(self, text: str) -> None:
        self.root.after(0, lambda: messagebox.showerror("Error", text))
        self._set_status("Error.")

    @staticmethod
    def _parse_optional_float(field_name: str, raw_value: str) -> Optional[float]:
        token = raw_value.strip()
        if not token:
            return None
        try:
            return float(token)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be a valid number.") from exc

    def _collect_manual_overrides(self, addresses: Optional[List[int]] = None) -> Dict[int, Dict[str, float]]:
        selected_addresses = [1, 2] if addresses is None else addresses
        vars_by_address = {
            1: (self.ps1_voltage_var.get(), self.ps1_current_var.get()),
            2: (self.ps2_voltage_var.get(), self.ps2_current_var.get()),
        }

        overrides: Dict[int, Dict[str, float]] = {}
        for address in selected_addresses:
            raw_voltage, raw_current = vars_by_address[address] #tuple
            voltage = self._parse_optional_float(f"PS{address} Voltage", raw_voltage)
            current = self._parse_optional_float(f"PS{address} Current", raw_current)

            selected: Dict[str, float] = {}
            if voltage is not None:
                selected["voltage"] = voltage
            if current is not None:
                selected["current"] = current
            if selected:
                overrides[address] = selected
        return overrides

    def _resolve_csv_path(self) -> str:
        folder = self.folder_var.get().strip() or self.script_dir
        os.makedirs(folder, exist_ok=True)

        if self.use_timestamp.get():
            filename = f"TDK_{datetime.datetime.now().strftime('%m%d%Y_%H%M%S')}"
            self.csv_name_var.set(filename)
        else:
            filename = self.csv_name_var.get().strip()
            if not filename:
                raise ValueError("CSV file name is required when timestamp mode is off.")

        if not filename.lower().endswith(".csv"):
            filename = f"{filename}.csv"
        return os.path.join(folder, filename)

    def _parse_sample_period(self) -> float:
        try:
            sample_period_s = float(self.sample_period_var.get().strip())
        except ValueError as exc:
            raise ValueError("Sample value must be numeric.") from exc

        if sample_period_s < 0:
            raise ValueError("Sample value must be zero or positive.")
        return sample_period_s

    @staticmethod
    def _apply_overrides(tdk: TDKLambda, overrides: Dict[int, Dict[str, float]]) -> None:
        for address, settings in overrides.items():
            if "voltage" in settings:
                tdk.set_voltage(address=address, voltage=settings["voltage"])
            if "current" in settings:
                tdk.set_current(address=address, current=settings["current"])

    @staticmethod
    def _safe_limits_for_address(address: int) -> Dict[str, float]:
        limits = HV_FALLBACK_LIMITS_BY_ADDRESS.get(int(address), HV_DEFAULT_FALLBACK_LIMITS)
        return {
            "voltage": float(limits["voltage"]),
            "current": float(limits["current"]),
        }

    def _collect_pre_on_warning_messages(
        self,
        tdk: TDKLambda,
        addresses: List[int],
        action_label: str,
    ) -> List[str]:
        warning_messages: List[str] = []
        for address in addresses:
            safe_limits = self._safe_limits_for_address(address)
            voltage = tdk.get_programmed_voltage_setpoint(address)
            current = tdk.get_programmed_current_setpoint(address)
            if voltage is None or current is None:
                warning_messages.append(
                    f"WARNING [{action_label}] PS{address} pre-ON setpoint readback unavailable."
                )
                continue

            if voltage > safe_limits["voltage"] or current > safe_limits["current"]:
                warning_messages.append(
                    f"WARNING [{action_label}] PS{address} pre-ON setpoint "
                    f"V={voltage:.3f}V I={current:.6f}A exceeds safe guidance "
                    f"V<={safe_limits['voltage']:.3f}V I<={safe_limits['current']:.6f}A."
                )
        return warning_messages

    def _emit_warning_messages(self, warning_messages: List[str]) -> str:
        if not warning_messages:
            return ""

        for message in warning_messages:
            print(message)
        summary = warning_messages[-1]
        self._set_status(summary)
        if len(warning_messages) == 1:
            return summary
        return f"{len(warning_messages)} warnings (latest: {summary})"

    def start_scan(self) -> None:
        """Start the background scan worker.

        Key inputs:
        - hv_mode: enables HV diagnostics, but Start remains logging-only.
        - mode/manual_overrides: forwarded for consistent logging session metadata.
        - stop_flag: cleared here so the new worker loop can run.
        """
        if self.scan_thread is not None and self.scan_thread.is_alive():
            self._set_status("Scan already running.")
            return

        hv_mode = self.hv_mode_var.get()

        try:
            csv_path = self._resolve_csv_path()
            sample_period_s = self._parse_sample_period()
            mode = self.mode_var.get().strip().lower()
            manual_overrides = self._collect_manual_overrides() if mode == "manual" and not hv_mode else {}
        except Exception as exc:
            messagebox.showerror("Error", str(exc))
            return

        self.stop_flag.clear()
        self._set_status("Scan running (logging-only)...")
        self.scan_thread = threading.Thread(
            target=self.run_scan,
            args=(
                csv_path,
                mode,
                manual_overrides,
                sample_period_s,
                hv_mode,
                self.hv_arm_var.get(),
                False,
            ),
            daemon=True,
        )
        self.scan_thread.start()

    def stop_scan(self) -> None:
        """Request cooperative stop for the active logging thread."""
        self.stop_flag.set()
        self._set_status("Stopping...")

    def _cancel_output_automation(self):
        if self.output_automation_thread is not None and self.output_automation_thread.is_alive():
            self.stop_flag.set()

    def run_scan(
        self,
        csv_path: str,
        mode: str,
        manual_overrides: Dict[int, Dict[str, float]],
        sample_period_s: float,
        hv_mode: bool,
        hv_armed: bool,
        start_outputs: bool,
    ) -> None:
        """Run one scan session.

        Key controls passed into TDKLogic:
        - mode/manual_overrides: non-HV behavior and optional per-PS settings.
        - hv_mode/hv_armed: whether the HV state machine is enabled/authorized.
        - start_outputs: True only for explicit ON workflows.
        - stop_flag: cooperative stop signal consumed by the logging loop.
        """
        try:
            result = run_logging_session(
                csv_path=csv_path,
                port=self.port_var.get().strip(),
                candidate_addresses=[1, 2],
                sample_period_s=sample_period_s,
                stop_event=self.stop_flag,
                mode=mode,
                manual_overrides=manual_overrides,
                hv_mode=hv_mode,
                hv_armed=hv_armed,
                # instrument_query reads max limits from PSU; fallback uses static map.
                hv_limit_source="fallback",  # Test mode: change to "fallback". and if we don't want fallback we use "instrument_query".
                print_rows=True,
                tdk=self._get_shared_tdk(),
                io_lock=self.tdk_lock,
                close_session=False,
                full_diag_every_n=10,
                hv_full_diag_every_n=2,
                control_request_event=self.control_request_event,
                control_timing=self.control_timing,
                control_timing_lock=self.control_timing_lock,
                status_callback=self._set_status,
                start_outputs=start_outputs,
            )
            if result.get("hv_arm_consumed"):
                self.root.after(0, self._consume_hv_arm)

            if result.get("tripped"):
                reason = str(result.get("trip_reason") or "HV rail triggered.")
                self._set_status(reason)
                return

            if self.stop_flag.is_set():
                self._set_status("Stopped.")
            else:
                self._set_status(f"Logging ended. File: {os.path.basename(csv_path)}")
        except Exception as exc:
            self._show_error(str(exc))

    def _start_control_action(
        self,
        action_label: str,
        action: Callable[[TDKLambda], Optional[str]],
        consume_hv_arm: bool = False,
    ) -> None:
        """Dispatch one control command on a background worker thread."""
        self._set_status(f"{action_label} running...")
        thread = threading.Thread(
            target=self._run_control_action_worker,
            args=(action_label, action, consume_hv_arm),
            daemon=True,
        )
        self.control_threads.append(thread)
        thread.start()

    def _run_control_action_worker(
        self,
        action_label: str,
        action: Callable[[TDKLambda], Optional[str]],
        consume_hv_arm: bool,
    ) -> None:
        """Execute control action with timing markers for latency diagnostics."""
        dispatch_ts = time.time()
        with self.control_timing_lock:
            marker_id = int(self.control_timing.get("marker_id", 0)) + 1
            self.control_timing["marker_id"] = marker_id
            self.control_timing["dispatch_ts"] = dispatch_ts
            self.control_timing["first_measure_ts"] = None
            self.control_timing["delta_ms"] = None
            self.control_timing["label"] = action_label
            self.control_timing["awaiting_measurement"] = True

        print(f"CONTROL_DISPATCH [{action_label}] ts={dispatch_ts:.3f}")
        self.control_request_event.set()
        try:
            tdk = self._get_shared_tdk()
            action_detail = ""
            with self.tdk_lock:
                result = action(tdk)
                if result:
                    action_detail = str(result).strip()
            if consume_hv_arm:
                self.root.after(0, self._consume_hv_arm)
            print(f"CONTROL_COMPLETE [{action_label}] ts={time.time():.3f}")
            if action_detail:
                self._set_status(f"{action_label} complete. {action_detail}")
            else:
                self._set_status(f"{action_label} complete.")
        except Exception as exc:
            self._show_error(str(exc))
        finally:
            self.control_request_event.clear()

    def handle_global_on(self) -> None:
        """Handle Global ON for HV automation or non-HV direct control."""
        hv_mode = self.hv_mode_var.get()
        if hv_mode:
            if not self._is_hv_armed_for_on("Global ON"):
                return
            if self.scan_thread is not None and self.scan_thread.is_alive():
                self._set_status("HV automation already running.")
                return
            try:
                csv_path = self._resolve_csv_path()
                sample_period_s = self._parse_sample_period()
            except Exception as exc:
                messagebox.showerror("Error", str(exc))
                return

            # HV path: Global ON becomes the automation trigger.
            self.stop_flag.clear()
            self._set_status("HV automation running...")
            self.scan_thread = threading.Thread(
                target=self.run_scan,
                args=(
                    csv_path,
                    "auto",
                    {},
                    sample_period_s,
                    True,
                    True,
                    True,
                ),
                daemon=True,
            )
            self.output_automation_thread = self.scan_thread
            self.scan_thread.start()
            self._consume_hv_arm()
            return

        mode = self.mode_var.get().strip().lower()
        try:
            overrides = (
                self._collect_manual_overrides([1, 2])
                if mode == "manual"
                else {}
            )
        except Exception as exc:
            messagebox.showerror("Error", str(exc))
            return

        def action(tdk: TDKLambda) -> Optional[str]:
            details: List[str] = []
            warning_messages = self._collect_pre_on_warning_messages(
                tdk=tdk,
                addresses=[1, 2],
                action_label="Global ON",
            )
            warning_detail = self._emit_warning_messages(warning_messages)
            if warning_detail:
                details.append(warning_detail)
            if mode == "manual":
                self._apply_overrides(tdk, overrides)
            tdk.global_on()
            return " | ".join(details) if details else None

        self._start_control_action("Global ON", action, consume_hv_arm=False)

    def handle_global_off(self) -> None:
        """Stop HV automation (if active) and issue global OFF."""
        self._cancel_output_automation()
        self._start_control_action("Global OFF", lambda tdk: tdk.global_off())

    def handle_ps_on(self, address: int) -> None:
        """Handle per-supply ON for non-HV workflows."""
        if self.hv_mode_var.get():
            self._set_status("PS TURN ON disabled in HV mode. Use Global TURN ON.")
            return

        mode = self.mode_var.get().strip().lower()
        try:
            overrides = (
                self._collect_manual_overrides([address])
                if mode == "manual"
                else {}
            )
        except Exception as exc:
            messagebox.showerror("Error", str(exc))
            return

        def action(tdk: TDKLambda) -> Optional[str]:
            details: List[str] = []
            warning_messages = self._collect_pre_on_warning_messages(
                tdk=tdk,
                addresses=[address],
                action_label=f"PS{address} ON",
            )
            warning_detail = self._emit_warning_messages(warning_messages)
            if warning_detail:
                details.append(warning_detail)
            if mode == "manual":
                self._apply_overrides(tdk, overrides)
            tdk.turn_on(address=address)
            return " | ".join(details) if details else None

        self._start_control_action(
            f"PS{address} ON",
            action,
            consume_hv_arm=False,
        )

    def handle_ps_off(self, address):
        """Stop any active automation and issue a per-supply OFF."""
        self._cancel_output_automation()
        self._start_control_action(
            f"PS{address} OFF",
            lambda tdk: tdk.turn_off(address=address),
            consume_hv_arm=False,
        )

    def on_closing(self) -> None:
        self.stop_flag.set()

        if self.scan_thread and self.scan_thread.is_alive():
            self.scan_thread.join(timeout=10)

        for thread in list(self.control_threads):
            if thread.is_alive():
                thread.join(timeout=3)

        self._close_shared_tdk()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = TDKGUI(root)
    root.mainloop()