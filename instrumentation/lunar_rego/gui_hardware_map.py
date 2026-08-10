"""
GUI-only hardware map for LunarRego diagnostics (LP / EP / RPA).

Does not talk to instruments. Stores which SMU/panel is assigned to each
diagnostic role, whether a backend exists, and shared-2410 notices.
Map sharing (e.g. LP REAR + RPA FRONT on one 2410) is allowed; exclusive
use is enforced only when two roles try to run at once.
"""

from __future__ import annotations

import copy
import json
import os
from typing import Any, Dict, List, Optional, Tuple

PACKAGE_DIR = os.path.abspath(os.path.dirname(__file__))
DEFAULT_MAP_PATH = os.path.join(PACKAGE_DIR, "hardware_map.json")

# Instruments the GUI may list. Only READY backends can run.
INSTRUMENT_CHOICES = [
    "2410",
    "2400",
    "2400-LV",
    "Siglent3303",
    "SiglentSPD1168",
    "HP6032A",
    "Fluke8846A",
    "None",
]

PANEL_CHOICES = ["FRONT", "REAR", "NA"]

BACKEND_READY = {
    "2410": True,
    "2400": True,
    "2400-LV": True,
    "Siglent3303": False,
    "SiglentSPD1168": False,
    "HP6032A": False,
    "Fluke8846A": False,
    "None": False,
}

# Companion picoammeter defaults when role uses 2400-LV collector.
DEFAULT_6485 = {
    "instrument": "6485",
    "GPIB": "15",
    "board": 2,
    "backend_ready": True,
}

ROLE_KEYS = ("LP", "EP", "RPA_P0", "RPA_P1", "RPA_P2", "RPA_P3", "RPA_P4")

RPA_PLATE_LABELS = {
    "RPA_P0": "P0 (plate 1)",
    "RPA_P1": "P1 (plate 2)",
    "RPA_P2": "P2 (plate 3)",
    "RPA_P3": "P3 (plate 4)",
    "RPA_P4": "P4 Collector",
}


def default_hardware_map() -> Dict[str, Any]:
    return {
        "LP": {
            "label": "Langmuir Probe",
            "instrument": "2410",
            "panel": "REAR",
            "GPIB": "1",
            "board": 0,
            "companion": None,
        },
        "EP": {
            "label": "Emissive Probe",
            "instrument": "HP6032A",
            "panel": "NA",
            "GPIB": "",
            "board": 0,
            "companion": {"instrument": "Fluke8846A", "GPIB": "", "board": 0},
        },
        "RPA_P0": {
            "label": RPA_PLATE_LABELS["RPA_P0"],
            "instrument": "2410",
            "panel": "FRONT",
            "GPIB": "1",
            "board": 0,
            "companion": None,
        },
        "RPA_P1": {
            "label": RPA_PLATE_LABELS["RPA_P1"],
            "instrument": "Siglent3303",
            "panel": "NA",
            "GPIB": "",
            "board": 0,
            "companion": None,
            "note": "ch.2",
        },
        "RPA_P2": {
            "label": RPA_PLATE_LABELS["RPA_P2"],
            "instrument": "SiglentSPD1168",
            "panel": "NA",
            "GPIB": "",
            "board": 0,
            "companion": None,
        },
        "RPA_P3": {
            "label": RPA_PLATE_LABELS["RPA_P3"],
            "instrument": "2400",
            "panel": "FRONT",
            "GPIB": "24",
            "board": 1,
            "companion": None,
        },
        "RPA_P4": {
            "label": RPA_PLATE_LABELS["RPA_P4"],
            "instrument": "2400-LV",
            "panel": "FRONT",
            "GPIB": "26",
            "board": 2,
            "companion": dict(DEFAULT_6485),
        },
    }


def backend_status(instrument: str) -> str:
    name = (instrument or "None").strip()
    if BACKEND_READY.get(name, False):
        return "Ready"
    return "Blocked"


def is_backend_ready(instrument: str) -> bool:
    return BACKEND_READY.get((instrument or "None").strip(), False)


def role_is_runnable(entry: Dict[str, Any]) -> bool:
    return is_backend_ready(str(entry.get("instrument", "None")))


def find_2410_shared_roles(hw_map: Dict[str, Any]) -> List[str]:
    """Informational: 2410 may be wired to several roles (e.g. LP REAR + RPA FRONT).

    Sharing in the map is allowed. Only simultaneous runs are exclusive — that is
    enforced at Start via find_runtime_2410_clash, not here.
    """
    claimants: List[str] = []
    for role in ROLE_KEYS:
        entry = hw_map.get(role) or {}
        if str(entry.get("instrument", "")).strip() == "2410":
            panel = str(entry.get("panel", "NA"))
            claimants.append(f"{role} ({panel})")
    if len(claimants) >= 2:
        return [
            "Note: Keithley 2410 is shared by "
            + ", ".join(claimants)
            + ". Mapping is fine; only one of those roles may run at a time "
            "(starting another will offer to override / stop the active run)."
        ]
    return []


def find_2410_conflicts(hw_map: Dict[str, Any]) -> List[str]:
    """Backward-compatible alias — shared-map notice, not a hard error."""
    return find_2410_shared_roles(hw_map)


def find_runtime_2410_clash(
    hw_map: Dict[str, Any],
    starting_role: str,
    active_roles: List[str],
) -> Optional[str]:
    """If starting_role needs 2410 and another active role also uses 2410, return message."""
    start_entry = hw_map.get(starting_role) or {}
    if str(start_entry.get("instrument", "")).strip() != "2410":
        return None
    for role in active_roles:
        if role == starting_role:
            continue
        other = hw_map.get(role) or {}
        if str(other.get("instrument", "")).strip() == "2410":
            return (
                f"2410 is currently owned by {role}. "
                f"Starting {starting_role} will stop that run and apply "
                f"{starting_role}'s map settings (panel / GPIB / board)."
            )
    return None


def roles_claiming_2410(hw_map: Dict[str, Any], roles: List[str]) -> List[str]:
    """Subset of roles whose map entry currently assigns the 2410."""
    claimed: List[str] = []
    for role in roles:
        entry = hw_map.get(role) or {}
        if str(entry.get("instrument", "")).strip() == "2410":
            claimed.append(role)
    return claimed


def load_hardware_map(path: Optional[str] = None) -> Dict[str, Any]:
    map_path = path or DEFAULT_MAP_PATH
    base = default_hardware_map()
    if not os.path.isfile(map_path):
        return base
    try:
        with open(map_path, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return base
    if not isinstance(loaded, dict):
        return base
    # Merge so new roles/defaults survive older JSON files.
    merged = copy.deepcopy(base)
    for role, entry in loaded.items():
        if role in merged and isinstance(entry, dict):
            merged[role].update(entry)
    return merged


def save_hardware_map(hw_map: Dict[str, Any], path: Optional[str] = None) -> str:
    map_path = path or DEFAULT_MAP_PATH
    with open(map_path, "w", encoding="utf-8") as handle:
        json.dump(hw_map, handle, indent=2)
    return map_path


def summarize_role(entry: Dict[str, Any]) -> str:
    inst = entry.get("instrument", "None")
    panel = entry.get("panel", "NA")
    gpib = entry.get("GPIB", "")
    board = entry.get("board", "")
    status = backend_status(str(inst))
    return f"{inst} {panel} | GPIB{board}::{gpib} | {status}"
