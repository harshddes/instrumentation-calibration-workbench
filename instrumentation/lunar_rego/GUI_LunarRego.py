"""
LunarRego diagnostics GUI — LP / EP / RPA + Hardware Map.

DAQ/TDK-style chrome: folder + CSV + Start/Stop + worker threads + stop_event.
Only Keithley backends are runnable; Siglent / HP / Fluke stay greyed out.
Does not modify SMU driver internals — GUI selects addresses, terminals, CSV.

Hardware map may assign one physical SMU (e.g. 2410 FRONT+REAR) to multiple
roles. That is wiring truth, not a Start blocker. Exclusive ownership is
enforced only when two roles that need the same box try to run together;
the newly started suite may override (stop) the active owner.

RPA: each plate gets its own control panel for the mapped PSU/SMU. Panels
rebuild when the Hardware Map is Applied / Saved.
"""

from __future__ import annotations

import copy
import csv
import datetime
import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List, Optional

import numpy as np

PACKAGE_DIR = os.path.abspath(os.path.dirname(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(PACKAGE_DIR, ".."))
for path in (PACKAGE_DIR, PROJECT_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

from gui_hardware_map import (
    INSTRUMENT_CHOICES,
    PANEL_CHOICES,
    ROLE_KEYS,
    RPA_PLATE_LABELS,
    backend_status,
    find_2410_shared_roles,
    find_runtime_2410_clash,
    load_hardware_map,
    role_is_runnable,
    roles_claiming_2410,
    save_hardware_map,
    summarize_role,
)
from Keithley2400_all import Keithley2400_all
from Keithley2410_all import Keithley2410_all
from Keithley6485 import CURRENT_RANGE_LABELS, Keithley6485
from series2400_voltage_backend import CSV_HEADER_COMBINED

from Keithley2400_LV_all import Keithley2400LV_all

# Integration-rate bounds (power-line cycles). Free-typed in the GUI.
NPLC_SMU_MIN = 0.01
NPLC_SMU_MAX = 10.0
NPLC_6485_MIN = 0.01
# SCPI Table 14-4 caps NPLC at 6.0 (front-panel RATE menu allows higher).
NPLC_6485_MAX = 6.0

# RPA_P2 is the bias plate that is swept; its NPLC is the suite master.
RPA_NPLC_MASTER_PLATE = "RPA_P2"
RPA_SWEEP_ONLY_PLATES = frozenset({RPA_NPLC_MASTER_PLATE})


def _parse_nplc(raw, lo, hi, label="NPLC"):
    """Parse a typed NPLC and reject values outside the instrument window."""
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a number.") from exc
    if value < lo or value > hi:
        raise ValueError(f"{label} must be in [{lo:g}, {hi:g}].")
    return value


class RpaCombinedCsv:
    """Thread-safe RPA product CSV: Timestamp, Sweep_V (P2), Picoammeter_I (6485)."""

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.Lock()
        self._file = open(self.path, "w", newline="")
        self._writer = csv.writer(self._file)
        self._writer.writerow(CSV_HEADER_COMBINED)
        self._file.flush()
        self._closed = False

    def append_row(self, timestamp, sweep_v, pico_i="") -> None:
        with self._lock:
            if self._closed:
                return
            self._writer.writerow([timestamp, sweep_v, pico_i])
            self._file.flush()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                self._file.close()
            finally:
                self._closed = True


class LunarRegoGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("LunarRego Diagnostics — LP / EP / RPA")
        self.root.minsize(900, 700)

        self.hw_map: Dict[str, Any] = load_hardware_map()
        # Shared lock: only one of LP / RPA_P0 may own the 2410 at a time.
        self.resource_2410_owner: Optional[str] = None

        self.lp_stop = threading.Event()
        self.lp_thread: Optional[threading.Thread] = None

        self.rpa_stop_events: Dict[str, threading.Event] = {}
        self.rpa_threads: Dict[str, threading.Thread] = {}
        self.rpa_combined: Optional[RpaCombinedCsv] = None
        self._rpa_combined_guard = threading.Lock()

        self._build_shell()
        self._refresh_map_dependent_labels()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    # ------------------------------------------------------------------ shell
    def _build_shell(self) -> None:
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=6, pady=6)

        self.lp_frame = ttk.Frame(self.notebook)
        self.ep_frame = ttk.Frame(self.notebook)
        self.rpa_frame = ttk.Frame(self.notebook)
        self.map_frame = ttk.Frame(self.notebook)

        self.notebook.add(self.lp_frame, text="LP")
        self.notebook.add(self.ep_frame, text="EP")
        self.notebook.add(self.rpa_frame, text="RPA")
        self.notebook.add(self.map_frame, text="Hardware Map")

        self._build_lp_tab()
        self._build_ep_tab()
        self._build_rpa_tab()
        self._build_map_tab()

    def _any_run_active(self) -> bool:
        if self.lp_thread is not None and self.lp_thread.is_alive():
            return True
        return any(t.is_alive() for t in self.rpa_threads.values())

    def _active_2410_roles(self) -> List[str]:
        roles: List[str] = []
        if self.lp_thread is not None and self.lp_thread.is_alive():
            if str(self.hw_map.get("LP", {}).get("instrument", "")) == "2410":
                roles.append("LP")
        for plate, thread in self.rpa_threads.items():
            if thread.is_alive() and str(self.hw_map.get(plate, {}).get("instrument", "")) == "2410":
                roles.append(plate)
        return roles

    # --------------------------------------------------------------- LP tab
    def _build_lp_tab(self) -> None:
        f = self.lp_frame
        f.columnconfigure(1, weight=1)

        self.lp_folder = tk.StringVar(value=Keithley2410_all.default_csv_dir())
        self.lp_csv = tk.StringVar(value="")
        self.lp_use_ts = tk.BooleanVar(value=True)
        self.lp_gpib = tk.StringVar()
        self.lp_board = tk.StringVar()
        self.lp_compliance = tk.StringVar(value="0.01")
        self.lp_v_start = tk.StringVar(value="0")
        self.lp_v_stop = tk.StringVar(value="1")
        self.lp_v_step = tk.StringVar(value="0.5")
        self.lp_nplc = tk.StringVar(value="1")
        self.lp_sweep_mode = tk.StringVar(value="software")
        self.lp_terminals_label = tk.StringVar(value="")
        self.lp_title = tk.StringVar(value="Langmuir Probe")
        self.lp_status = tk.StringVar(value="Idle.")

        row = 0
        tk.Label(f, textvariable=self.lp_title, font=("", 11, "bold")).grid(
            row=row, column=0, columnspan=3, sticky="w", padx=4, pady=4
        )
        row += 1

        tk.Label(f, text="Save Folder:").grid(row=row, column=0, sticky="w", padx=4, pady=2)
        tk.Entry(f, textvariable=self.lp_folder, width=48).grid(
            row=row, column=1, sticky="we", padx=4, pady=2
        )
        tk.Button(f, text="Choose...", command=lambda: self._choose_folder(self.lp_folder)).grid(
            row=row, column=2, padx=4, pady=2
        )
        row += 1

        tk.Label(f, text="CSV File Name:").grid(row=row, column=0, sticky="w", padx=4, pady=2)
        self.lp_csv_entry = tk.Entry(f, textvariable=self.lp_csv)
        self.lp_csv_entry.grid(row=row, column=1, sticky="we", padx=4, pady=2)
        tk.Checkbutton(
            f,
            text="Use timestamped filename",
            variable=self.lp_use_ts,
            command=lambda: self._toggle_csv(self.lp_csv_entry, self.lp_use_ts),
        ).grid(row=row, column=2, sticky="w", padx=4, pady=2)
        row += 1

        tk.Label(f, text="GPIB:").grid(row=row, column=0, sticky="w", padx=4, pady=2)
        tk.Entry(f, textvariable=self.lp_gpib, width=8).grid(row=row, column=1, sticky="w", padx=4)
        row += 1
        tk.Label(f, text="Board:").grid(row=row, column=0, sticky="w", padx=4, pady=2)
        tk.Entry(f, textvariable=self.lp_board, width=8).grid(row=row, column=1, sticky="w", padx=4)
        row += 1

        tk.Label(f, text="Terminals (from map):").grid(row=row, column=0, sticky="w", padx=4, pady=2)
        tk.Label(f, textvariable=self.lp_terminals_label, fg="navy").grid(
            row=row, column=1, sticky="w", padx=4
        )
        row += 1

        tk.Label(f, text="Compliance (A):").grid(row=row, column=0, sticky="w", padx=4, pady=2)
        tk.Entry(f, textvariable=self.lp_compliance, width=12).grid(
            row=row, column=1, sticky="w", padx=4
        )
        row += 1

        sweep = tk.LabelFrame(f, text="Voltage sweep")
        sweep.grid(row=row, column=0, columnspan=3, sticky="we", padx=4, pady=6)
        tk.Radiobutton(
            sweep, text="Software linear list", variable=self.lp_sweep_mode, value="software"
        ).grid(row=0, column=0, sticky="w", padx=4)
        tk.Radiobutton(
            sweep, text="Instrument linear staircase", variable=self.lp_sweep_mode, value="linear"
        ).grid(row=0, column=1, sticky="w", padx=4)
        tk.Label(sweep, text="Start V").grid(row=1, column=0, sticky="w", padx=4)
        tk.Entry(sweep, textvariable=self.lp_v_start, width=10).grid(row=1, column=1, sticky="w")
        tk.Label(sweep, text="Stop V").grid(row=2, column=0, sticky="w", padx=4)
        tk.Entry(sweep, textvariable=self.lp_v_stop, width=10).grid(row=2, column=1, sticky="w")
        tk.Label(sweep, text="Step V").grid(row=3, column=0, sticky="w", padx=4)
        tk.Entry(sweep, textvariable=self.lp_v_step, width=10).grid(row=3, column=1, sticky="w")
        tk.Label(sweep, text="NPLC").grid(row=4, column=0, sticky="w", padx=4)
        tk.Entry(sweep, textvariable=self.lp_nplc, width=10).grid(
            row=4, column=1, sticky="w"
        )
        row += 1

        btns = tk.Frame(f)
        btns.grid(row=row, column=0, columnspan=3, sticky="w", padx=4, pady=6)
        tk.Button(btns, text="Start", width=10, command=self.start_lp).pack(side="left", padx=4)
        tk.Button(btns, text="Stop", width=10, command=self.stop_lp).pack(side="left", padx=4)
        row += 1
        tk.Label(f, textvariable=self.lp_status, anchor="w").grid(
            row=row, column=0, columnspan=3, sticky="we", padx=4, pady=4
        )
        self._toggle_csv(self.lp_csv_entry, self.lp_use_ts)

    def start_lp(self) -> None:
        if self.lp_thread is not None and self.lp_thread.is_alive():
            messagebox.showinfo("LP", "LP sweep already running.")
            return
        entry = self.hw_map.get("LP") or {}
        if not role_is_runnable(entry):
            messagebox.showerror("LP", "LP instrument has no backend (check Hardware Map).")
            return
        # Runtime only: if another suite currently owns the 2410, offer override.
        if str(entry.get("instrument")) == "2410":
            active = self._active_2410_roles()
            clash_msg = find_runtime_2410_clash(self.hw_map, "LP", active)
            if clash_msg:
                other = next((r for r in active if r != "LP"), "other")
                if messagebox.askyesno(
                    "2410 override",
                    f"{clash_msg}\n\nStop {other} and start LP with LP map settings?",
                ):
                    if other.startswith("RPA"):
                        self.stop_rpa()
                    else:
                        self.stop_lp()
                    self.root.after(200, self.start_lp)
                return

        try:
            csv_path = self._resolve_csv_path(
                self.lp_folder,
                self.lp_csv,
                self.lp_use_ts,
                prefix="LP",
            )
            lp_nplc = _parse_nplc(
                self.lp_nplc.get(),
                NPLC_SMU_MIN,
                NPLC_SMU_MAX,
                label="LP NPLC",
            )
            self.hw_map["LP"]["board"] = int(self.lp_board.get().strip())
        except ValueError as exc:
            messagebox.showerror("LP", str(exc))
            return

        self.hw_map["LP"]["GPIB"] = self.lp_gpib.get().strip()

        self.lp_stop.clear()
        self.lp_status.set("Starting LP…")
        self.lp_thread = threading.Thread(
            target=self._run_lp,
            args=(csv_path, lp_nplc),
            daemon=True,
        )
        self.lp_thread.start()

    def stop_lp(self) -> None:
        self.lp_stop.set()
        self.lp_status.set("Stop requested…")

    def _run_lp(self, csv_path: str, nplc: float) -> None:
        entry = self.hw_map["LP"]
        instrument = str(entry.get("instrument", "2410"))
        try:
            if instrument == "2410":
                self.resource_2410_owner = "LP"
            smu = self._build_smu(
                instrument=instrument,
                csv_path=csv_path,
                gpib=self.lp_gpib.get().strip(),
                board=int(self.lp_board.get().strip()),
                terminals=str(entry.get("panel", "REAR")),
                compliance=float(self.lp_compliance.get()),
                stop_event=self.lp_stop,
                companion=None,
                nplc=nplc,
            )
            self._set_status_safe(self.lp_status, f"Running → {csv_path}")
            self._execute_sweep(
                smu,
                mode=self.lp_sweep_mode.get(),
                v_start=float(self.lp_v_start.get()),
                v_stop=float(self.lp_v_stop.get()),
                v_step=float(self.lp_v_step.get()),
            )
            self._set_status_safe(self.lp_status, "Idle. Sweep finished.")
        except Exception as exc:
            self._set_status_safe(self.lp_status, f"Error: {exc}")
            self.root.after(0, lambda: messagebox.showerror("LP", str(exc)))
        finally:
            if self.resource_2410_owner == "LP":
                self.resource_2410_owner = None

    # --------------------------------------------------------------- EP tab
    def _build_ep_tab(self) -> None:
        f = self.ep_frame
        self.ep_title = tk.StringVar(value="Emissive Probe")
        self.ep_detail = tk.StringVar(value="")
        overlay = tk.Frame(f, bg="#d0d0d0")
        overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        tk.Label(
            overlay,
            textvariable=self.ep_title,
            bg="#d0d0d0",
            fg="#333333",
            font=("", 12, "bold"),
            justify="center",
        ).place(relx=0.5, rely=0.35, anchor="center")
        tk.Label(
            overlay,
            textvariable=self.ep_detail,
            bg="#d0d0d0",
            fg="#333333",
            font=("", 11),
            justify="center",
        ).place(relx=0.5, rely=0.48, anchor="center")
        tk.Button(overlay, text="Start", state="disabled").place(
            relx=0.5, rely=0.65, anchor="center"
        )

    # -------------------------------------------------------------- RPA tab
    RPA_PLATES = ("RPA_P0", "RPA_P1", "RPA_P2", "RPA_P3", "RPA_P4")
    KEITHLEY_SMUS = frozenset({"2410", "2400", "2400-LV"})

    def _build_rpa_tab(self) -> None:
        f = self.rpa_frame
        f.columnconfigure(0, weight=1)
        f.rowconfigure(3, weight=1)

        self.rpa_folder = tk.StringVar(value=PACKAGE_DIR)
        self.rpa_csv_prefix = tk.StringVar(value="RPA")
        self.rpa_use_ts = tk.BooleanVar(value=True)
        self.rpa_status = tk.StringVar(value="Idle.")
        self.rpa_title = tk.StringVar(value="Retarding Potential Analyzer")
        self.rpa_plate_ctrls: Dict[str, Dict[str, Any]] = {}
        self.rpa_enable: Dict[str, tk.BooleanVar] = {}

        row = 0
        tk.Label(f, textvariable=self.rpa_title, font=("", 11, "bold")).grid(
            row=row, column=0, sticky="w", padx=4, pady=4
        )
        row += 1

        top = tk.Frame(f)
        top.grid(row=row, column=0, sticky="we", padx=4)
        top.columnconfigure(1, weight=1)
        tk.Label(top, text="Save Folder (base):").grid(row=0, column=0, sticky="w")
        tk.Entry(top, textvariable=self.rpa_folder, width=48).grid(
            row=0, column=1, sticky="we", padx=4
        )
        tk.Button(top, text="Choose...", command=lambda: self._choose_folder(self.rpa_folder)).grid(
            row=0, column=2, padx=4
        )
        tk.Label(top, text="CSV name prefix:").grid(row=1, column=0, sticky="w", pady=2)
        self.rpa_csv_entry = tk.Entry(top, textvariable=self.rpa_csv_prefix)
        self.rpa_csv_entry.grid(row=1, column=1, sticky="we", padx=4)
        tk.Checkbutton(
            top,
            text="Timestamp filenames",
            variable=self.rpa_use_ts,
            command=lambda: self._toggle_csv(self.rpa_csv_entry, self.rpa_use_ts),
        ).grid(row=1, column=2, sticky="w")
        row += 1

        hint = tk.Label(
            f,
            text="P2 sweep owns NPLC + the 6485 (lockstep: set V → read pico). "
            "Combined CSV = Timestamp, Sweep_V, Picoammeter_I. "
            "Other plates: fixed V / own CSV only (6485 is not free-running on them).",
            fg="#555555",
            anchor="w",
            justify="left",
        )
        hint.grid(row=row, column=0, sticky="we", padx=4, pady=(0, 4))
        row += 1

        # Scrollable host so five instrument panels fit without crushing the window.
        outer = tk.Frame(f)
        outer.grid(row=row, column=0, sticky="nsew", padx=4, pady=2)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)
        canvas = tk.Canvas(outer, highlightthickness=0, height=420)
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        self.rpa_plates_host = tk.Frame(canvas)
        self.rpa_plates_host.bind(
            "<Configure>",
            lambda _e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        host_win = canvas.create_window((0, 0), window=self.rpa_plates_host, anchor="nw")
        canvas.bind(
            "<Configure>",
            lambda e: canvas.itemconfigure(host_win, width=e.width),
        )
        canvas.configure(yscrollcommand=sb.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        self.rpa_plates_canvas = canvas

        def _on_mousewheel(event):
            if self.notebook.index(self.notebook.select()) != self.notebook.index(self.rpa_frame):
                return
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        row += 1

        btns = tk.Frame(f)
        btns.grid(row=row, column=0, sticky="w", padx=4, pady=6)
        tk.Button(
            btns, text="Start all enabled", width=16, command=self.start_rpa
        ).pack(side="left", padx=4)
        tk.Button(
            btns, text="Stop all", width=10, command=self.stop_rpa
        ).pack(side="left", padx=4)
        row += 1
        tk.Label(f, textvariable=self.rpa_status, anchor="w").grid(
            row=row, column=0, sticky="we", padx=4
        )
        self._toggle_csv(self.rpa_csv_entry, self.rpa_use_ts)

    def _snapshot_rpa_plate_values(self) -> Dict[str, Dict[str, Any]]:
        """Keep user knobs across map rebuilds when the instrument family stays usable."""
        out: Dict[str, Dict[str, Any]] = {}
        for plate, ctrl in self.rpa_plate_ctrls.items():
            snap: Dict[str, Any] = {
                "instrument": ctrl.get("instrument"),
                "enable": bool(ctrl["enable"].get()) if "enable" in ctrl else False,
            }
            for key, var in ctrl.get("vars", {}).items():
                try:
                    snap[key] = var.get()
                except Exception:
                    pass
            out[plate] = snap
        return out

    def _rebuild_rpa_plate_panels(self) -> None:
        if not hasattr(self, "rpa_plates_host"):
            return
        prev = self._snapshot_rpa_plate_values()
        for child in self.rpa_plates_host.winfo_children():
            child.destroy()
        self.rpa_plate_ctrls.clear()
        self.rpa_enable.clear()

        runnable: List[str] = []
        for i, plate in enumerate(self.RPA_PLATES):
            entry = self.hw_map.get(plate) or {}
            instrument = str(entry.get("instrument", "None")).strip() or "None"
            ready = role_is_runnable(entry)
            snap = prev.get(plate) or {}
            keep_vals = snap.get("instrument") == instrument

            frame = tk.LabelFrame(
                self.rpa_plates_host,
                text=f"{RPA_PLATE_LABELS[plate]}  —  {summarize_role(entry)}",
                padx=4,
                pady=4,
            )
            frame.grid(row=i, column=0, sticky="we", padx=2, pady=4)
            frame.columnconfigure(1, weight=1)

            enable = tk.BooleanVar(value=False)
            self.rpa_enable[plate] = enable
            ctrl: Dict[str, Any] = {
                "instrument": instrument,
                "enable": enable,
                "frame": frame,
                "vars": {},
                "ready": ready,
            }
            self.rpa_plate_ctrls[plate] = ctrl

            if instrument in self.KEITHLEY_SMUS and ready:
                self._fill_keithley_plate_panel(plate, entry, ctrl, snap if keep_vals else {})
                if keep_vals:
                    enable.set(bool(snap.get("enable", False)))
                else:
                    enable.set(True)
                runnable.append(f"{RPA_PLATE_LABELS[plate]}:{instrument}")
            else:
                self._fill_blocked_plate_panel(plate, entry, ctrl)
                enable.set(False)

        if runnable:
            self.rpa_title.set(
                "Retarding Potential Analyzer — " + ", ".join(runnable)
            )
        else:
            self.rpa_title.set("Retarding Potential Analyzer — (no ready backends)")

    def _fill_blocked_plate_panel(
        self,
        plate: str,
        entry: Dict[str, Any],
        ctrl: Dict[str, Any],
    ) -> None:
        frame = ctrl["frame"]
        enable = ctrl["enable"]
        cb = tk.Checkbutton(
            frame,
            text="Enable (unavailable)",
            variable=enable,
            state="disabled",
        )
        cb.grid(row=0, column=0, columnspan=4, sticky="w")
        note = entry.get("note")
        msg = (
            f"No runnable backend for {entry.get('instrument', 'None')}. "
            "Change the Hardware Map assignment, or wait until this PSU is coded."
        )
        if note:
            msg += f"\nMap note: {note}"
        tk.Label(frame, text=msg, fg="#666666", justify="left", wraplength=700).grid(
            row=1, column=0, columnspan=4, sticky="w", padx=4, pady=2
        )

    def _fill_keithley_plate_panel(
        self,
        plate: str,
        entry: Dict[str, Any],
        ctrl: Dict[str, Any],
        snap: Dict[str, Any],
    ) -> None:
        frame = ctrl["frame"]
        enable = ctrl["enable"]
        instrument = ctrl["instrument"]
        vars_ = ctrl["vars"]
        sweep_only = plate in RPA_SWEEP_ONLY_PLATES

        def _sv(key, default):
            var = tk.StringVar(value=str(snap.get(key, default)))
            vars_[key] = var
            return var

        def _bv(key, default):
            var = tk.BooleanVar(value=bool(snap.get(key, default)))
            vars_[key] = var
            return var

        max_v = "?"
        try:
            max_v = str(self._smu_class(instrument).MAX_SOURCE_VOLTAGE)
        except KeyError:
            pass

        role_bits = [f"Terminals: {entry.get('panel', 'NA')}", f"Max source: {max_v} V"]
        if plate == RPA_NPLC_MASTER_PLATE:
            role_bits.append("NPLC master for RPA")
        elif not sweep_only:
            role_bits.append("NPLC follows P2")

        tk.Checkbutton(frame, text="Enable", variable=enable).grid(
            row=0, column=0, sticky="w"
        )
        tk.Label(frame, text="  |  ".join(role_bits), fg="navy").grid(
            row=0, column=1, columnspan=3, sticky="w", padx=8
        )
        plate_btns = tk.Frame(frame)
        plate_btns.grid(row=0, column=4, columnspan=2, sticky="e", padx=4)
        tk.Button(
            plate_btns,
            text="Start",
            width=8,
            command=lambda p=plate: self.start_rpa_plate(p),
        ).pack(side="left", padx=2)
        tk.Button(
            plate_btns,
            text="Stop",
            width=8,
            command=lambda p=plate: self.stop_rpa_plate(p),
        ).pack(side="left", padx=2)

        tk.Label(frame, text="GPIB").grid(row=1, column=0, sticky="w")
        gpib_var = tk.StringVar(value=str(snap.get("gpib", entry.get("GPIB", ""))))
        board_var = tk.StringVar(value=str(snap.get("board", entry.get("board", 0))))
        vars_["gpib"] = gpib_var
        vars_["board"] = board_var
        tk.Entry(frame, textvariable=gpib_var, width=8).grid(row=1, column=1, sticky="w")
        tk.Label(frame, text="Board").grid(row=1, column=2, sticky="w", padx=(8, 0))
        tk.Entry(frame, textvariable=board_var, width=6).grid(row=1, column=3, sticky="w")
        tk.Label(frame, text="Compliance A").grid(row=1, column=4, sticky="w", padx=(8, 0))
        tk.Entry(frame, textvariable=_sv("compliance", "0.01"), width=8).grid(
            row=1, column=5, sticky="w"
        )

        row = 2
        if sweep_only:
            # P2: always sweep — NPLC is the suite master.
            tk.Label(
                frame,
                text="Mode: voltage sweep (fixed V not available on P2)",
                fg="#333333",
            ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(4, 0))
            tk.Label(frame, text="NPLC (master)").grid(
                row=row, column=3, sticky="e", padx=4
            )
            tk.Entry(frame, textvariable=_sv("nplc", "1"), width=8).grid(
                row=row, column=4, sticky="w"
            )
            row += 1
            self._fill_rpa_sweep_controls(frame, vars_, snap, _sv, row=row)
            row += 2
        else:
            # Fixed V is primary; sweep lives under Advanced.
            tk.Label(frame, text="Fixed V").grid(row=row, column=0, sticky="w")
            tk.Entry(frame, textvariable=_sv("fixed_v", "0"), width=10).grid(
                row=row, column=1, sticky="w"
            )
            tk.Label(
                frame,
                text="NPLC: synced from P2 at Start",
                fg="#555555",
            ).grid(row=row, column=2, columnspan=3, sticky="w", padx=8)
            # Keep a hidden default so snapshots stay stable; never used at run.
            _sv("nplc", snap.get("nplc", "1"))
            row += 1

            adv_open = _bv("adv_open", False)
            use_sweep = _bv("use_sweep", False)
            adv_body = tk.Frame(frame, bd=1, relief="groove", padx=4, pady=4)

            def _toggle_adv(body=adv_body, flag=adv_open, r=row + 1):
                if flag.get():
                    body.grid(row=r, column=0, columnspan=6, sticky="we", pady=4)
                else:
                    body.grid_remove()

            tk.Checkbutton(
                frame,
                text="Advanced ▸ voltage sweep (optional)",
                variable=adv_open,
                command=_toggle_adv,
            ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(4, 0))
            row += 1

            tk.Checkbutton(
                adv_body,
                text="Run as sweep instead of fixed V",
                variable=use_sweep,
            ).grid(row=0, column=0, columnspan=4, sticky="w")
            self._fill_rpa_sweep_controls(adv_body, vars_, snap, _sv, row=1)
            if adv_open.get():
                adv_body.grid(row=row, column=0, columnspan=6, sticky="we", pady=4)
            row += 1

        companion = entry.get("companion") or {}
        comp_inst = str(companion.get("instrument", "")).strip() if companion else ""
        if companion and comp_inst in ("6485", "Keithley6485"):
            box = tk.LabelFrame(
                frame,
                text=(
                    f"Companion {comp_inst}  |  "
                    f"GPIB{companion.get('board', '')}::{companion.get('GPIB', '')}  |  "
                    f"NPLC synced from P2"
                ),
            )
            box.grid(row=row, column=0, columnspan=6, sticky="we", pady=4)
            tk.Checkbutton(
                box, text="Auto range", variable=_bv("comp_auto", True)
            ).grid(row=0, column=0, padx=8)
            tk.Label(box, text="Manual range").grid(row=0, column=1, sticky="w")
            range_var = _sv("comp_range", CURRENT_RANGE_LABELS[0])
            tk.OptionMenu(box, range_var, *CURRENT_RANGE_LABELS).grid(
                row=0, column=2, padx=4
            )
            tk.Checkbutton(
                box,
                text="Zero-correct on connect",
                variable=_bv("comp_zero", False),
            ).grid(row=0, column=3, padx=8)
            ctrl["has_6485"] = True
        else:
            ctrl["has_6485"] = False

    def _fill_rpa_sweep_controls(self, parent, vars_, snap, _sv, row: int = 0) -> None:
        """Shared Start/Stop/Step + host-timed sweep radios."""
        if "sweep_mode" not in vars_:
            mode = tk.StringVar(value=str(snap.get("sweep_mode", "software")))
            vars_["sweep_mode"] = mode
        else:
            mode = vars_["sweep_mode"]
        tk.Label(parent, text="Sweep").grid(row=row, column=0, sticky="w")
        tk.Radiobutton(
            parent, text="Software list", variable=mode, value="software"
        ).grid(row=row, column=1, sticky="w")
        tk.Radiobutton(
            parent, text="Instrument linear", variable=mode, value="linear"
        ).grid(row=row, column=2, columnspan=2, sticky="w")
        recipe = (
            ("Start V", "v_start", "0"),
            ("Stop V", "v_stop", "1"),
            ("Step V", "v_step", "0.5"),
        )
        for col, (label, key, default) in enumerate(recipe):
            tk.Label(parent, text=label).grid(row=row + 1, column=col, sticky="w", padx=2)
            tk.Entry(parent, textvariable=_sv(key, default), width=8).grid(
                row=row + 2, column=col, sticky="w", padx=2, pady=(0, 2)
            )

    def _rpa_plate_thread_alive(self, plate: str) -> bool:
        thread = self.rpa_threads.get(plate)
        return thread is not None and thread.is_alive()

    def _rpa_runnable_plates(self, require_enable: bool) -> List[str]:
        plates: List[str] = []
        for plate in self.RPA_PLATES:
            ctrl = self.rpa_plate_ctrls.get(plate)
            if ctrl is None:
                continue
            if require_enable and not ctrl["enable"].get():
                continue
            entry = self.hw_map.get(plate) or {}
            if not role_is_runnable(entry):
                continue
            if str(entry.get("instrument", "")).strip() not in self.KEITHLEY_SMUS:
                continue
            plates.append(plate)
        return plates

    def _ensure_2410_ok_for_start(self, plate: str, retry=None) -> bool:
        """Return True if safe to start now. On override, stop peers and schedule retry."""
        entry = self.hw_map.get(plate) or {}
        if str(entry.get("instrument", "")).strip() != "2410":
            return True
        active = [r for r in self._active_2410_roles() if r != plate]
        clash_msg = find_runtime_2410_clash(self.hw_map, plate, active)
        if not clash_msg:
            return True
        if messagebox.askyesno(
            "2410 override",
            f"{clash_msg}\n\nStop the other 2410 owner and start {plate}?",
        ):
            if "LP" in active:
                self.stop_lp()
            for other in active:
                if other != "LP":
                    self.stop_rpa_plate(other)
            if retry is not None:
                self.root.after(250, retry)
            return False
        return False

    def start_rpa_plate(self, plate: str) -> None:
        """Start one RPA plate's SMU/PSU sweep (independent of other plates)."""
        ctrl = self.rpa_plate_ctrls.get(plate)
        if ctrl is None:
            messagebox.showerror("RPA", f"No panel for {plate}.")
            return
        entry = self.hw_map.get(plate) or {}
        instrument = str(entry.get("instrument", "")).strip()
        if instrument not in self.KEITHLEY_SMUS or not role_is_runnable(entry):
            messagebox.showwarning(
                plate,
                f"{instrument or 'None'} has no runnable backend.\n"
                "Fix the Hardware Map assignment for this plate.",
            )
            return
        if self._rpa_plate_thread_alive(plate):
            messagebox.showinfo(plate, "This plate is already running.")
            return
        if not self._ensure_2410_ok_for_start(
            plate, retry=lambda p=plate: self.start_rpa_plate(p)
        ):
            return
        try:
            knobs = self._rpa_plate_knobs(plate)
        except ValueError as exc:
            messagebox.showerror(plate, str(exc))
            return
        self._launch_rpa_plate(plate, knobs)

    def stop_rpa_plate(self, plate: str) -> None:
        """Request stop for one plate; other plates keep running."""
        ev = self.rpa_stop_events.get(plate)
        if ev is None or not self._rpa_plate_thread_alive(plate):
            self.rpa_status.set(f"{plate}: not running.")
            return
        ev.set()
        self.rpa_status.set(f"{plate}: stop requested…")

    def start_rpa(self) -> None:
        """Start every Enable-checked plate that is not already running."""
        enabled = self._rpa_runnable_plates(require_enable=True)
        if not enabled:
            messagebox.showwarning(
                "RPA",
                "No enabled plates with a ready backend.\n"
                "Enable coded plates or fix Hardware Map.",
            )
            return

        rpa_2410 = roles_claiming_2410(self.hw_map, enabled)
        if len(rpa_2410) > 1:
            messagebox.showerror(
                "RPA",
                "Multiple enabled RPA plates claim the 2410: "
                + ", ".join(rpa_2410)
                + ".\nDisable all but one (cannot run FRONT+REAR together).",
            )
            return

        to_start: List[str] = []
        knobs_by_plate: Dict[str, Dict[str, Any]] = {}
        for plate in enabled:
            if self._rpa_plate_thread_alive(plate):
                continue
            if not self._ensure_2410_ok_for_start(plate, retry=self.start_rpa):
                return
            try:
                knobs_by_plate[plate] = self._rpa_plate_knobs(plate)
            except ValueError as exc:
                messagebox.showerror(plate, str(exc))
                return
            to_start.append(plate)

        if not to_start:
            messagebox.showinfo("RPA", "All enabled plates are already running.")
            return

        self.rpa_status.set(f"Starting plates: {', '.join(to_start)}")
        for plate in to_start:
            self._launch_rpa_plate(plate, knobs_by_plate[plate])

    def stop_rpa(self) -> None:
        for plate in list(self.rpa_stop_events.keys()):
            self.stop_rpa_plate(plate)
        self.rpa_status.set("Stop requested for all RPA plates…")

    def _launch_rpa_plate(self, plate: str, knobs: Dict[str, Any]) -> None:
        # Combined science CSV is produced only by the P2 sweep + 6485 lockstep.
        combined = None
        if plate == RPA_NPLC_MASTER_PLATE:
            combined = self._ensure_rpa_combined_csv()
        stop_ev = threading.Event()
        self.rpa_stop_events[plate] = stop_ev
        thread = threading.Thread(
            target=self._run_rpa_plate,
            args=(plate, stop_ev, knobs, combined),
            daemon=True,
        )
        self.rpa_threads[plate] = thread
        thread.start()

    def _find_rpa_6485_host(self):
        """Return (host_plate, host_map_entry) for the mapped 6485 companion, if any."""
        for plate in self.RPA_PLATES:
            entry = self.hw_map.get(plate) or {}
            companion = entry.get("companion") or {}
            inst = str(companion.get("instrument", "")).strip()
            if inst in ("6485", "Keithley6485"):
                return plate, entry
        return None, None

    def _rpa_6485_knobs_from_host(self, host_plate: str, master_nplc: float) -> Dict[str, Any]:
        """Read 6485 UI knobs from the host plate; force NPLC from P2 master."""
        knobs = {
            "comp_nplc": min(float(master_nplc), NPLC_6485_MAX),
            "comp_auto": True,
            "comp_range": CURRENT_RANGE_LABELS[0],
            "comp_zero": False,
        }
        ctrl = self.rpa_plate_ctrls.get(host_plate) or {}
        vars_ = ctrl.get("vars") or {}
        if "comp_auto" in vars_:
            knobs["comp_auto"] = bool(vars_["comp_auto"].get())
        if "comp_range" in vars_:
            knobs["comp_range"] = str(vars_["comp_range"].get())
        if "comp_zero" in vars_:
            knobs["comp_zero"] = bool(vars_["comp_zero"].get())
        if knobs["comp_nplc"] < float(master_nplc):
            print(
                f"6485 NPLC clamped {master_nplc:g} → {knobs['comp_nplc']:g} "
                f"(SCPI max {NPLC_6485_MAX:g})"
            )
        return knobs

    def _ensure_rpa_combined_csv(self) -> RpaCombinedCsv:
        """Open (or reopen) the P2 science CSV for this sweep."""
        with self._rpa_combined_guard:
            if self.rpa_combined is not None and not self.rpa_combined._closed:
                return self.rpa_combined

            folder = self.rpa_folder.get().strip() or PACKAGE_DIR
            os.makedirs(folder, exist_ok=True)
            prefix = self.rpa_csv_prefix.get().strip() or "RPA"
            if self.rpa_use_ts.get():
                stamp = datetime.datetime.now().strftime("%m%d%Y_%H%M%S")
                name = f"{prefix}_combined_{stamp}.csv"
            else:
                name = f"{prefix}_combined.csv"
            path = os.path.join(folder, name)
            self.rpa_combined = RpaCombinedCsv(path)
            print(f"RPA combined CSV: {path}")
            return self.rpa_combined

    def _close_rpa_combined_csv(self) -> None:
        """Close the science CSV when P2 sweep ends (P4 bias may still be running)."""
        with self._rpa_combined_guard:
            if self.rpa_combined is not None and not self.rpa_combined._closed:
                path = self.rpa_combined.path
                self.rpa_combined.close()
                print(f"RPA combined CSV closed: {path}")

    def _rpa_master_nplc(self) -> float:
        """P2 defines RPA integration time; every other instrument clones it."""
        ctrl = self.rpa_plate_ctrls.get(RPA_NPLC_MASTER_PLATE)
        if ctrl is not None and "nplc" in ctrl.get("vars", {}):
            return _parse_nplc(
                ctrl["vars"]["nplc"].get(),
                NPLC_SMU_MIN,
                NPLC_SMU_MAX,
                label="P2 NPLC (master)",
            )
        return 1.0

    def _rpa_plate_knobs(self, plate: str) -> Dict[str, Any]:
        ctrl = self.rpa_plate_ctrls[plate]
        vars_ = ctrl["vars"]
        entry = self.hw_map.get(plate) or {}
        sweep_only = plate in RPA_SWEEP_ONLY_PLATES
        try:
            board = int(str(vars_["board"].get()).strip())
            gpib = str(vars_["gpib"].get()).strip()
            if not gpib:
                raise ValueError(
                    "GPIB primary address is empty. "
                    "Set the address in this plate's GPIB box (or Hardware Map), "
                    "or set the plate instrument to None if that SMU is disconnected."
                )

            master_nplc = self._rpa_master_nplc()
            smu_nplc = min(master_nplc, NPLC_SMU_MAX)
            if smu_nplc < master_nplc:
                print(
                    f"{plate}: SMU NPLC clamped {master_nplc:g} → {smu_nplc:g} "
                    f"(max {NPLC_SMU_MAX:g})"
                )

            if sweep_only:
                run_mode = "sweep"
                fixed_v = 0.0
            elif bool(vars_["use_sweep"].get()):
                run_mode = "sweep"
                fixed_v = float(vars_["fixed_v"].get())
            else:
                run_mode = "fixed"
                fixed_v = float(vars_["fixed_v"].get())

            knobs = {
                "gpib": gpib,
                "board": board,
                "panel": str(entry.get("panel", "FRONT")),
                "run_mode": run_mode,
                "fixed_v": fixed_v,
                "sweep_mode": str(vars_["sweep_mode"].get()),
                "v_start": float(vars_["v_start"].get()),
                "v_stop": float(vars_["v_stop"].get()),
                "v_step": float(vars_["v_step"].get()),
                "compliance": float(vars_["compliance"].get()),
                "nplc": smu_nplc,
                "master_nplc": master_nplc,
                "has_6485": bool(ctrl.get("has_6485")),
            }
        except (ValueError, KeyError) as exc:
            raise ValueError(str(exc)) from exc

        if knobs["has_6485"]:
            comp_nplc = min(master_nplc, NPLC_6485_MAX)
            if comp_nplc < master_nplc:
                print(
                    f"{plate}: 6485 NPLC clamped {master_nplc:g} → {comp_nplc:g} "
                    f"(SCPI max {NPLC_6485_MAX:g})"
                )
            knobs["comp_nplc"] = comp_nplc
            knobs["comp_auto"] = bool(vars_["comp_auto"].get())
            knobs["comp_range"] = str(vars_["comp_range"].get())
            knobs["comp_zero"] = bool(vars_["comp_zero"].get())
        return knobs

    def _run_rpa_plate(
        self,
        plate: str,
        stop_ev: threading.Event,
        knobs: Dict[str, Any],
        combined: Optional[RpaCombinedCsv] = None,
    ) -> None:
        entry = self.hw_map[plate]
        instrument = str(entry.get("instrument"))
        companion = None
        try:
            cls = self._smu_class(instrument)
            stamp = datetime.datetime.now().strftime("%m%d%Y_%H%M%S")
            if self.rpa_use_ts.get():
                name = f"{self.rpa_csv_prefix.get().strip() or 'RPA'}_{plate}_{stamp}.csv"
            else:
                base = self.rpa_csv_prefix.get().strip() or "RPA"
                name = f"{base}_{plate}.csv"
            csv_path = cls.default_csv_path(name)

            # Persist GPIB/board edits back into the live map for this role.
            self.hw_map[plate]["GPIB"] = knobs["gpib"]
            self.hw_map[plate]["board"] = knobs["board"]

            if instrument == "2410":
                self.resource_2410_owner = plate

            master_nplc = float(knobs.get("master_nplc", knobs["nplc"]))
            host_plate, host_entry = self._find_rpa_6485_host()

            # P2 borrows the mapped 6485 and reads it on every sweep step (lockstep).
            # The host plate (usually P4) must NOT free-run the same meter.
            if plate == RPA_NPLC_MASTER_PLATE and host_entry is not None:
                pico_knobs = self._rpa_6485_knobs_from_host(host_plate, master_nplc)
                companion = self._build_6485_companion(host_entry, pico_knobs)
                print(
                    f"{plate}: lockstep 6485 from {host_plate} | "
                    f"SMU nplc={knobs['nplc']:g} | 6485 nplc={pico_knobs['comp_nplc']:g}"
                )
            elif host_plate is not None and plate == host_plate:
                print(
                    f"{plate}: 6485 reserved for P2 lockstep — "
                    "this plate biases only (no picoammeter free-run)."
                )
            elif knobs.get("has_6485"):
                companion = self._build_6485_companion(entry, knobs)
                print(
                    f"{plate}: local companion 6485 | nplc={knobs.get('comp_nplc', master_nplc):g}"
                )
            else:
                print(
                    f"{plate}: NPLC master (P2)={master_nplc:g} | SMU nplc={knobs['nplc']:g}"
                )

            if combined is not None:
                print(f"{plate}: combined science CSV → {combined.path}")

            smu = self._build_smu(
                instrument=instrument,
                csv_path=csv_path,
                gpib=knobs["gpib"],
                board=knobs["board"],
                terminals=knobs["panel"],
                compliance=knobs["compliance"],
                stop_event=stop_ev,
                companion=companion,
                nplc=knobs["nplc"],
                role_label=plate,
                combined_csv=combined if plate == RPA_NPLC_MASTER_PLATE else None,
            )
            status = f"{plate} running → {csv_path}"
            if combined is not None and plate == RPA_NPLC_MASTER_PLATE:
                status += f" | combined={os.path.basename(combined.path)}"
            self._set_status_safe(self.rpa_status, status)
            self._execute_plate_run(smu, knobs)
        except Exception as exc:
            self._set_status_safe(self.rpa_status, f"{plate} error: {exc}")
            self.root.after(0, lambda e=exc, p=plate: messagebox.showerror(p, str(e)))
        finally:
            if companion is not None:
                try:
                    companion.set_zero_check(True)
                    companion.inst.close()
                except Exception as close_exc:
                    print(f"{plate} 6485 close:", close_exc)
            if self.resource_2410_owner == plate:
                self.resource_2410_owner = None
            if plate == RPA_NPLC_MASTER_PLATE:
                self._close_rpa_combined_csv()
            if not any(
                t.is_alive()
                for t in self.rpa_threads.values()
                if t is not threading.current_thread()
            ):
                self._set_status_safe(self.rpa_status, "Idle. RPA plates finished.")

    def _build_6485_companion(self, plate_entry: Dict[str, Any], knobs: Dict[str, Any]):
        comp = plate_entry.get("companion") or {}
        gpib = str(comp.get("GPIB", "15"))
        board = int(comp.get("board", 2))
        auto = bool(knobs.get("comp_auto", True))
        range_arg = None if auto else knobs.get("comp_range")
        return Keithley6485(
            csv_title=None,
            GPIB=gpib,
            board=board,
            nplc=float(knobs.get("comp_nplc", 1.0)),
            auto_range=auto,
            current_range=range_arg,
            zero_correct_on_connect=bool(knobs.get("comp_zero", False)),
        )

    # ------------------------------------------------------- Hardware Map
    def _build_map_tab(self) -> None:
        f = self.map_frame
        f.columnconfigure(0, weight=1)

        self.map_conflict_var = tk.StringVar(value="")
        self.map_conflict_label = tk.Label(
            f, textvariable=self.map_conflict_var, fg="#8a5a00", wraplength=720, justify="left"
        )
        self.map_conflict_label.grid(row=0, column=0, sticky="we", padx=6, pady=4)

        self.map_rows: Dict[str, Dict[str, Any]] = {}
        table = tk.Frame(f)
        table.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)

        headers = ("Role", "Instrument", "Panel", "GPIB", "Board", "Backend")
        for c, h in enumerate(headers):
            tk.Label(table, text=h, font=("", 9, "bold")).grid(row=0, column=c, padx=4, pady=2)

        for r, role in enumerate(ROLE_KEYS, start=1):
            entry = self.hw_map[role]
            inst_var = tk.StringVar(value=str(entry.get("instrument", "None")))
            panel_var = tk.StringVar(value=str(entry.get("panel", "NA")))
            gpib_var = tk.StringVar(value=str(entry.get("GPIB", "")))
            board_var = tk.StringVar(value=str(entry.get("board", 0)))
            status_var = tk.StringVar(value=backend_status(inst_var.get()))

            tk.Label(table, text=role, width=10, anchor="w").grid(row=r, column=0, sticky="w")
            om = tk.OptionMenu(
                table,
                inst_var,
                *INSTRUMENT_CHOICES,
                command=lambda _v, role=role: self._on_map_field_changed(role),
            )
            om.grid(row=r, column=1, sticky="w")
            pom = tk.OptionMenu(
                table,
                panel_var,
                *PANEL_CHOICES,
                command=lambda _v, role=role: self._on_map_field_changed(role),
            )
            pom.grid(row=r, column=2, sticky="w")
            tk.Entry(table, textvariable=gpib_var, width=8).grid(row=r, column=3)
            tk.Entry(table, textvariable=board_var, width=6).grid(row=r, column=4)
            tk.Label(table, textvariable=status_var, width=10).grid(row=r, column=5)

            self.map_rows[role] = {
                "instrument": inst_var,
                "panel": panel_var,
                "GPIB": gpib_var,
                "board": board_var,
                "status": status_var,
            }
            gpib_var.trace_add("write", lambda *_a, role=role: self._on_map_field_changed(role))
            board_var.trace_add("write", lambda *_a, role=role: self._on_map_field_changed(role))

        btns = tk.Frame(f)
        btns.grid(row=2, column=0, sticky="w", padx=4, pady=8)
        tk.Button(btns, text="Apply / Save Map", command=self.save_map_from_ui).pack(
            side="left", padx=4
        )
        tk.Button(btns, text="Reload Defaults", command=self.reload_default_map).pack(
            side="left", padx=4
        )
        tk.Label(
            f,
            text="Changing the map while a run is active is refused until Stop.",
            fg="#555555",
        ).grid(row=3, column=0, sticky="w", padx=6)

        self._update_conflict_banner()

    def _on_map_field_changed(self, role: str) -> None:
        row = self.map_rows[role]
        row["status"].set(backend_status(row["instrument"].get()))
        # Live preview of shared-2410 notice from UI values without writing disk yet.
        preview = self._map_from_ui()
        shared = find_2410_shared_roles(preview)
        self.map_conflict_var.set(shared[0] if shared else "")

    def _map_from_ui(self) -> Dict[str, Any]:
        updated = copy.deepcopy(self.hw_map)
        for role, row in self.map_rows.items():
            updated[role]["instrument"] = row["instrument"].get().strip()
            updated[role]["panel"] = row["panel"].get().strip()
            updated[role]["GPIB"] = row["GPIB"].get().strip()
            try:
                updated[role]["board"] = int(row["board"].get().strip())
            except ValueError:
                updated[role]["board"] = row["board"].get().strip()
        return updated

    def save_map_from_ui(self) -> None:
        if self._any_run_active():
            messagebox.showerror(
                "Hardware Map",
                "A measurement is running. Stop LP / RPA before changing the map.",
            )
            return
        updated = self._map_from_ui()
        for role, entry in updated.items():
            if not isinstance(entry.get("board"), int):
                messagebox.showerror("Hardware Map", f"{role}: board must be an integer.")
                return
        self.hw_map = updated
        path = save_hardware_map(self.hw_map)
        self._refresh_map_dependent_labels()
        self._update_conflict_banner()
        messagebox.showinfo("Hardware Map", f"Saved to\n{path}")

    def reload_default_map(self) -> None:
        if self._any_run_active():
            messagebox.showerror("Hardware Map", "Stop runs before reloading defaults.")
            return
        from gui_hardware_map import default_hardware_map

        self.hw_map = default_hardware_map()
        for role, row in self.map_rows.items():
            entry = self.hw_map[role]
            row["instrument"].set(str(entry.get("instrument", "None")))
            row["panel"].set(str(entry.get("panel", "NA")))
            row["GPIB"].set(str(entry.get("GPIB", "")))
            row["board"].set(str(entry.get("board", 0)))
            row["status"].set(backend_status(row["instrument"].get()))
        self._refresh_map_dependent_labels()
        self._update_conflict_banner()

    def _update_conflict_banner(self) -> None:
        shared = find_2410_shared_roles(self.hw_map)
        self.map_conflict_var.set(shared[0] if shared else "")

    def _refresh_map_dependent_labels(self) -> None:
        lp = self.hw_map.get("LP") or {}
        lp_inst = str(lp.get("instrument", "None"))
        lp_panel = str(lp.get("panel", "NA"))
        self.lp_gpib.set(str(lp.get("GPIB", "1")))
        self.lp_board.set(str(lp.get("board", 0)))
        self.lp_terminals_label.set(lp_panel)
        self.lp_title.set(f"Langmuir Probe — {lp_inst} ({lp_panel})")
        try:
            cls = self._smu_class(lp_inst)
            self.lp_folder.set(cls.default_csv_dir())
        except KeyError:
            pass

        ep = self.hw_map.get("EP") or {}
        ep_inst = str(ep.get("instrument", "None"))
        ep_comp = ep.get("companion") or {}
        ep_comp_inst = str(ep_comp.get("instrument", "None")) if ep_comp else "None"
        self.ep_title.set(f"Emissive Probe — {ep_inst}")
        if role_is_runnable(ep):
            detail = f"Mapped: {summarize_role(ep)}"
            if ep_comp:
                detail += f"\nCompanion: {ep_comp_inst}"
            self.ep_detail.set(detail)
        else:
            self.ep_detail.set(
                f"Mapped: {summarize_role(ep)}"
                + (f"\nCompanion: {ep_comp_inst}" if ep_comp else "")
                + "\n\nBackend not configured. Coming later. Start is disabled."
            )

        # RPA plate panels morph to whichever PSU/SMU the map assigned.
        self._rebuild_rpa_plate_panels()

        self._update_conflict_banner()
        if self.lp_status.get().startswith("Warning:"):
            self.lp_status.set("Idle.")
        if self.rpa_status.get().startswith("Warning:"):
            self.rpa_status.set("Idle.")

    # ----------------------------------------------------------- helpers
    def _smu_class(self, instrument: str):
        mapping = {
            "2410": Keithley2410_all,
            "2400": Keithley2400_all,
            "2400-LV": Keithley2400LV_all,
        }
        if instrument not in mapping:
            raise KeyError(f"No SMU class for instrument {instrument!r}")
        return mapping[instrument]

    def _build_smu(
        self,
        instrument: str,
        csv_path: str,
        gpib: str,
        board: int,
        terminals: str,
        compliance: float,
        stop_event: threading.Event,
        companion=None,
        nplc: float = 1.0,
        role_label: Optional[str] = None,
        combined_csv=None,
    ):
        cls = self._smu_class(instrument)
        panel = terminals if terminals in ("FRONT", "REAR") else "REAR"
        return cls(
            csv_title=csv_path,
            GPIB=gpib,
            board=board,
            terminals=panel,
            compliance_current=compliance,
            stop_event=stop_event,
            companion_ammeter=companion,
            nplc=nplc,
            role_label=role_label,
            combined_csv=combined_csv,
        )

    def _execute_plate_run(self, smu, knobs: Dict[str, Any]) -> None:
        """Fixed hold (primary) or host-timed voltage list (sweep / P2)."""
        if knobs.get("run_mode") == "fixed":
            print(f"GUI: fixed voltage hold at {knobs['fixed_v']} V until Stop.")
            smu.start_fixed(float(knobs["fixed_v"]))
            return
        self._execute_sweep(
            smu,
            mode=knobs["sweep_mode"],
            v_start=knobs["v_start"],
            v_stop=knobs["v_stop"],
            v_step=knobs["v_step"],
        )

    def _execute_sweep(
        self,
        smu,
        mode: str,
        v_start: float,
        v_stop: float,
        v_step: float,
    ) -> None:
        if v_step == 0:
            raise ValueError("Step V must be non-zero for the voltage list.")
        n = int(round(abs(v_stop - v_start) / abs(v_step))) + 1
        voltages = np.linspace(v_start, v_stop, max(n, 1))

        # Instrument staircase uses one blocking :READ? for the whole sweep.
        # Stop cannot interrupt that wait, and a 6485 companion cannot sample
        # per step after the fact. GUI always steps on the host so Stop / I_6485
        # stay responsive between points.
        if mode == "linear":
            print(
                "GUI: host-timed linear steps "
                "(Stop + companion ammeter stay responsive between points)."
            )
        smu.start_software_sweep(voltages)

    def _resolve_csv_path(
        self,
        folder_var: tk.StringVar,
        csv_var: tk.StringVar,
        use_ts: tk.BooleanVar,
        prefix: str,
    ) -> str:
        folder = folder_var.get().strip()
        if not folder:
            raise ValueError("Save folder is required.")
        os.makedirs(folder, exist_ok=True)
        if use_ts.get():
            now = datetime.datetime.now()
            name = f"{prefix}_{now.strftime('%m%d%Y_%H%M%S')}.csv"
            csv_var.set(name)
        else:
            name = csv_var.get().strip()
            if not name:
                raise ValueError("CSV file name is required when timestamp mode is off.")
            if not name.lower().endswith(".csv"):
                name = f"{name}.csv"
        return os.path.join(folder, name)

    def _choose_folder(self, var: tk.StringVar) -> None:
        folder = filedialog.askdirectory(initialdir=var.get() or PACKAGE_DIR)
        if folder:
            var.set(folder)

    @staticmethod
    def _toggle_csv(entry: tk.Entry, use_ts: tk.BooleanVar) -> None:
        entry.configure(state="disabled" if use_ts.get() else "normal")

    def _set_status_safe(self, var: tk.StringVar, text: str) -> None:
        self.root.after(0, lambda: var.set(text))

    def on_closing(self) -> None:
        self.lp_stop.set()
        for ev in self.rpa_stop_events.values():
            ev.set()
        if self.lp_thread is not None and self.lp_thread.is_alive():
            self.lp_thread.join(timeout=10)
        for thread in self.rpa_threads.values():
            if thread.is_alive():
                thread.join(timeout=10)
        if self.rpa_combined is not None and not self.rpa_combined._closed:
            self.rpa_combined.close()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = LunarRegoGUI(root)
    root.mainloop()
