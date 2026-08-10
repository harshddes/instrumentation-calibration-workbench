import csv
import os
import re
import sys
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pyvisa

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from instrumentation.snapshot import publish_snapshot, read_snapshot, compute_freshness
'''
Shared helper usage contract:
- Keep `snapshot` as the canonical variable name for read_snapshot(...) output.
- TDK side publishes latest row via publish_snapshot(snapshot_path, row, sequence).
- DAQ side reads once per row and calls compute_freshness(snapshot, daq_timestamp, ...).
'''

csv_filename = os.path.join(PROJECT_ROOT, "tdklambda", "data", "tdkMeasure_gui.csv")
if not os.path.exists(csv_filename):
    with open(csv_filename, "w", newline=""):
        pass
SYST_ERR_TIMEOUT = "Timeout while reading SYST:ERR?"

# Production HV trip thresholds.
# For temporary bench testing, adjust these three values together, then restore:
#   HV_TRIP1_MAXV = 200.0
#   HV_TRIP2_MIN_I = 1.0
#   HV_TRIP3_AVGV = 15.0
# HV_TRIP1_MAXV = 200.0 #trip1_maxV, NOT WORKING RIGHT NOW, internal LOGIC COMMENTED
HV_TRIP2_MIN_I = 0.5 #trip1_minI
# HV_TRIP3_AVGV = 1 #trip1_avgV , NOT WORKING RIGHT NOW, internal LOGIC COMMENTED

# Trip-evaluator timing knobs:
# - HV_AVG_WINDOW_SECONDS controls the rolling V-average guard window.
# - HV_TRIP_PERSIST_SECONDS requires a condition to stay active before tripping.
HV_AVG_WINDOW_SECONDS = 2.0 #HV_AVG_WINDOW
HV_TRIP_PERSIST_SECONDS = 2.0 #HV_TRIP_PERSIST
ADDRESS_VERIFY_BACKOFF_SECONDS = (0.0, 0.002, 0.005, 0.01, 0.02)
DEFAULT_FULL_DIAG_EVERY_N = 10
HV_FULL_DIAG_EVERY_N = 2
HV_VERIFY_MAX_OVERSHOOT_V = 0.05
HV_VERIFY_MAX_OVERSHOOT_A = 0.0005
TDK_SETUP_POST_CLEAR_DELAY_SECONDS = 0.25
VISA_OPEN_RETRY_DELAYS = (0.0, 0.25, 0.5, 1.0)
HV_PHASE1_STABLE_SECONDS = 20.0
# Adaptive retry knobs for phase-2 backoff behavior.
HV_RETRY_BASE_COOLDOWN_SECONDS = 5.0
HV_RETRY_GUARD_WINDOW_SECONDS = 30.0
HV_RETRY_MAX_COOLDOWN_SECONDS = 15.0
HV_RETRY_ESCALATION_LEVELS: List[Tuple[int, float]] = [(8, 15.0), (5, 10.0), (3, 8.0)]
HV_PHASE3_TIE_DELTA_V = 0.05
HV_PHASE3_SHUTOFF_POLICY = "lower_voltage"
HV_PHASE3_ALLOWED_POLICIES = ("lower_voltage", "always_ps1")

# Production fallback limits used when instrument max-query is unavailable.
# For temporary HV tests, switch GUI hv_limit_source to "fallback" and edit
# this map plus HV_DEFAULT_FALLBACK_LIMITS as a matched profile.
HV_FALLBACK_LIMITS_BY_ADDRESS: Dict[int, Dict[str, float]] = {
    1: {"voltage": 450.0, "current": 1.3},
    2: {"voltage": 450.0, "current": 1.3},
}
HV_DEFAULT_FALLBACK_LIMITS: Dict[str, float] = {"voltage": 50.0, "current": 0.005}
SWEEP_NATIVE_MAX_POINTS = 12
SWEEP_POLL_SECONDS = 0.25
SWEEP_ALLOWED_MODES = ("list", "wave", "fix", "software")
SWEEP_ALLOWED_STEP_MODES = ("AUTO", "ONCE")

# Fault register bit map (Questionable Condition/FLT(fault) register).
# Memory camera, "what WAS?"
FAULT_BITS = {
    # 0: "NOT_USED",
    1: "AC_FAIL", 
    2: "OVER_TEMP",
    3: "FOLDBACK",
    4: "OVP",
    5: "SHUT_OFF",
    6: "OUTPUT_OFF",
    7: "INTERLOCK",
    8: "UVP", # This is UVP, not your imaginary "SAFE_START"
    # 9: "NOT_USED",
    10: "INPUT_OVERFLOW",
    11: "INTERNAL_OVERFLOW",
    12: "INTERNAL_TIMEOUT",
    13: "INTERNAL_COMM_ERROR",
}

# Operation condition register bit map (STAT:OPER:COND?).
#Live, Realtime registers and sensors
OPER_BITS = {
    0: "CV_MODE",
    1: "CC_MODE",
    2: "NO_FAULT",
    3: "TRIGGER_WAIT",
    4: "AUTO_START",
    5: "FOLDBACK_ENABLED",
    6: "LIST_STEP_COMPLETE",
    7: "LOCAL_MODE",
    8: "UVP_ENABLED",
    9: "INTERLOCK_ENABLED",
    # 10 is Reserved / Empty
    11: "FBC_ENABLED",
    12: "ANALOG_VOLTAGE_PROGRAM",
    13: "ANALOG_CURRENT_PROGRAM",
    14: "LIST_DWELL_ACTIVE",
}


def _is_visa_busy(exc) -> bool:
    """Return True when VISA reports a resource-lock conflict."""
    return "VI_ERROR_RSRC_BUSY" in str(exc)


def decode_bits(value: Optional[int], bit_map: Dict[int, str]) -> str:
    '''     This piece of code is the exact "cashier" at the weird restaurant we just talked about. Its entire job is to take the total bill, look at the menu, and print out an itemized receipt so you don't have to do the math.

        Let's break down this code line-by-line using the same logic. 

        ### The Setup
        ```python
        def decode_bits(value: Optional[int], bit_map: Dict[int, str]) -> str:
        ```
        You are hiring the cashier (the function). You hand them two things:
        1.  **`value`**: The total bill the power supply yelled at you (e.g., **18**).
        2.  **`bit_map`**: The menu. This is just a Python dictionary that maps the switch number to the name of the fault. For example: `{1: "AC Fail", 4: "OVP"}`.

        ---

        ### Step 1: The Bouncer
        ```python
            if value is None:
                return "NA"
        ```
        The cashier expects a number. If the power supply glitches and hands over thin air (`None`) instead of a receipt, the cashier refuses to do the math, yells "Not Applicable" (`"NA"`), and quits.

        ---

        ### Step 2: The Cashier's Brain (The Core Logic)
        ```python
            active = [name for bit, name in bit_map.items() if value & (1 << bit)]
        ```
        This single line looks like a nightmare, but it is just a lazy Python shortcut (called a list comprehension) for writing a `for` loop. The cashier is creating an itemized list called `active`. 

        Here is exactly what the cashier is doing, read from left to right:
        * **`for bit, name in bit_map.items()`**: The cashier runs their finger down every single item on the menu, one by one. *"Okay, let's look at Switch #1 (AC Fail). Now let's look at Switch #2 (Over Temp)..."*
        * **`if value & (1 << bit)`**: The Interrogation. For every item they point at, they ask the magic bitwise question: *"Does the price of this specific switch (`1 << bit`) perfectly fit inside the total bill (`value`)?"*
            * *Example:* If the bill is 18, and they are looking at Switch 4, they ask: *"Does 16 (`1 << 4`) fit inside 18?"* Yes.
        * **`[name ...]`**: If the answer is yes, the cashier writes the `name` (e.g., "OVP") down on the blank `active` list.

        By the end of this line, `active` is just a simple list of the things that went wrong, like: `["AC Fail", "OVP"]`.

        ---

        ### Step 3: Printing the Receipt
        ```python
            return ",".join(active) if active else "NONE"
        ```
        The cashier looks at the `active` list they just wrote. 
        * **`if active`**: If they actually wrote anything down, they glue the names together with commas and hand you the receipt: `"AC Fail,OVP"`.
        * **`else "NONE"`**: If the list is completely blank (meaning the bill was **0**), the power supply is perfectly healthy. The cashier just yells `"NONE"`.

        That is it. The code is just a highly efficient idiot tracking a menu to see which prices add up to your total bill.

        Would you like me to write out the exact `bit_map` menu for your Z+ power supply so you can copy/paste it into your script and actually run this cashier?
    '''
    if value is None:
        return "NA"
    active = [name for bit, name in bit_map.items() if value & (1 << bit)] #list comprehension
    return ",".join(active) if active else "NONE"

# formatting function. It is a defensive perimeter.
def fmt_value(value: Any, digits: int = 5) -> str:
    if value is None:
        return "NA"
    token = str(value).strip()
    try:
        return f"{float(token):.{digits}f}"
    except (TypeError, ValueError):
        return token


def parse_numeric_value(value: Any) -> Optional[float]:
    if value is None:
        return None

    token = str(value).strip().strip('"')
    if not token:
        return None

    first = token.split(",")[0].strip()
    try:
        return float(first)
    except ValueError:
        match = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", first)
        if not match:
            return None
        try:
            return float(match.group(0))
        except ValueError:
            return None


def _normalize_sweep_mode(mode):
    mode_normalized = str(mode or "").strip().lower()
    aliases = {
        "list step": "list",
        "list": "list",
        "step": "list",
        "wave ramp": "wave",
        "wave": "wave",
        "ramp": "wave",
        "fix trigger": "fix",
        "fix": "fix",
        "software custom": "software",
        "software": "software",
    }
    selected = aliases.get(mode_normalized)
    if selected not in SWEEP_ALLOWED_MODES:
        raise ValueError(
            f"Unsupported sweep mode '{mode}'. Use list, wave, fix, or software."
        )
    return selected


def _normalize_sweep_step_mode(step_mode):
    selected = str(step_mode or "AUTO").strip().upper()
    if selected not in SWEEP_ALLOWED_STEP_MODES:
        raise ValueError("Sweep step mode must be AUTO or ONCE.")
    return selected


def _split_sweep_recipe_line(line):
    if "," in line:
        return [field.strip() for field in next(csv.reader([line]))]
    return [field.strip() for field in line.split()]


def _is_sweep_header(fields):
    joined = " ".join(fields).strip().lower()
    return bool(joined and any(token in joined for token in ("address", "voltage", "current", "duration")))


def _parse_sweep_float(field_name, raw_value, allow_blank=True):
    token = str(raw_value or "").strip()
    if token.lower() in {"", "none", "na", "n/a", "-"}:
        if allow_blank:
            return None
        raise ValueError(f"{field_name} is required.")
    try:
        return float(token)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be numeric.") from exc


def parse_sweep_recipe_text(raw_text, default_addresses=None):
    """Parse GUI sweep rows into address/voltage/current/duration dictionaries."""
    default_address_list = [1] if default_addresses is None else [int(x) for x in default_addresses]
    points: List[Dict[str, Any]] = []
    for line_number, raw_line in enumerate(str(raw_text or "").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = _split_sweep_recipe_line(line)
        if _is_sweep_header(fields):
            continue

        if len(fields) >= 4:
            try:
                addresses = [int(fields[0])]
            except ValueError as exc:
                raise ValueError(f"Line {line_number}: address must be an integer.") from exc
            value_fields = fields[1:4]
        elif len(fields) == 3:
            if not default_address_list:
                raise ValueError(
                    f"Line {line_number}: addressed-row mode requires "
                    "address,voltage,current,duration_s."
                )
            addresses = list(default_address_list)
            value_fields = fields
        else:
            raise ValueError(
                f"Line {line_number}: use voltage,current,duration_s or "
                "address,voltage,current,duration_s."
            )

        voltage = _parse_sweep_float(f"Line {line_number} voltage", value_fields[0])
        current = _parse_sweep_float(f"Line {line_number} current", value_fields[1])
        duration_s = _parse_sweep_float(
            f"Line {line_number} duration_s",
            value_fields[2],
            allow_blank=False,
        )

        for address in addresses:
            points.append(
                {
                    "address": int(address),
                    "voltage": voltage,
                    "current": current,
                    "duration_s": duration_s,
                    "line_number": line_number,
                }
            )

    if not points:
        raise ValueError("Sweep recipe is empty.")
    return points


def normalize_sweep_points(points, mode="software"):
    mode_normalized = _normalize_sweep_mode(mode)
    normalized: List[Dict[str, Any]] = []
    for index, point in enumerate(points, start=1):
        try:
            address = int(point.get("address"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Sweep point {index}: address must be an integer.") from exc

        voltage = point.get("voltage")
        current = point.get("current")
        duration_s = point.get("duration_s", 0.0)
        if voltage is not None:
            voltage = float(voltage)
        if current is not None:
            current = float(current)
        if voltage is None and current is None:
            raise ValueError(f"Sweep point {index}: voltage or current is required.")

        duration_s = 0.0 if duration_s is None else float(duration_s)
        if mode_normalized != "fix" and duration_s <= 0:
            raise ValueError(f"Sweep point {index}: duration_s must be greater than zero.")
        if mode_normalized == "fix" and duration_s < 0:
            raise ValueError(f"Sweep point {index}: duration_s cannot be negative.")

        normalized.append(
            {
                "address": address,
                "voltage": voltage,
                "current": current,
                "duration_s": duration_s,
            }
        )
    return normalized


def _group_sweep_points_by_address(points):
    grouped: Dict[int, List[Dict[str, Any]]] = {}
    for point in points:
        grouped.setdefault(int(point["address"]), []).append(point)
    return grouped


def _non_null_values(rows, key):
    return [row[key] for row in rows if row.get(key) is not None]


def _unique_float_values(values):
    return sorted({round(float(value), 12) for value in values})


def _select_wave_axis(rows):
    voltage_values = _non_null_values(rows, "voltage")
    current_values = _non_null_values(rows, "current")
    voltage_unique = _unique_float_values(voltage_values)
    current_unique = _unique_float_values(current_values)
    voltage_changes = len(voltage_unique) > 1
    current_changes = len(current_unique) > 1
    if voltage_changes and current_changes:
        raise ValueError("WAVE mode can ramp voltage or current, but not both at once.")
    if voltage_values and not current_changes:
        return "voltage"
    if current_values:
        return "current"
    raise ValueError("WAVE mode requires voltage or current points.")


def sweep_native_possible(mode, points):
    mode_normalized = _normalize_sweep_mode(mode)
    if mode_normalized == "software":
        return False
    grouped = _group_sweep_points_by_address(points)
    for rows in grouped.values():
        if len(rows) > SWEEP_NATIVE_MAX_POINTS:
            return False
        if mode_normalized == "fix" and len(rows) != 1:
            return False
        if mode_normalized == "list":
            voltage_values = _non_null_values(rows, "voltage")
            current_values = _non_null_values(rows, "current")
            if voltage_values and len(voltage_values) != len(rows):
                return False
            if current_values and len(current_values) != len(rows):
                return False
        if mode_normalized == "wave":
            wave_axis = _select_wave_axis(rows)
            wave_values = _non_null_values(rows, wave_axis)
            if len(wave_values) != len(rows):
                return False
    return True


def summarize_sweep_plan(mode, points, native_preferred=True):
    mode_normalized = _normalize_sweep_mode(mode)
    normalized = normalize_sweep_points(points, mode_normalized)
    grouped = _group_sweep_points_by_address(normalized)
    address_parts = [
        f"PS{address}: {len(rows)} point(s)"
        for address, rows in sorted(grouped.items())
    ]
    native_ready = native_preferred and sweep_native_possible(mode_normalized, normalized)
    engine = "native TDK" if native_ready else "software"
    return f"{mode_normalized.upper()} sweep using {engine}; " + ", ".join(address_parts)


class TDKLambda:
    def __init__(self, port = "ASRL4::INSTR", address: Optional[int] = None):
        """Open one VISA session with retry for transient busy-port locks."""
        self.address: Optional[int] = int(address) if address is not None else None
        self.idn: Optional[str] = None
        self._selected_address_cache = None if self.address is None else int(self.address)
        self._last_measurement_cache: Dict[int, Dict[str, Any]] = {}
        self._known_addresses: List[int] = []

        self.rm = pyvisa.ResourceManager()
        self.psu = None
        last_busy_exc = None
        for wait_s in VISA_OPEN_RETRY_DELAYS:
            if wait_s > 0:
                time.sleep(wait_s)
            try:
                self.psu = self.rm.open_resource(
                    port,
                    baud_rate=9600,
                    read_termination="\r\n",
                    write_termination="\r\n",
                )
                last_busy_exc = None
                break
            except pyvisa.errors.VisaIOError as exc:
                if not _is_visa_busy(exc):
                    try:
                        self.rm.close()
                    except Exception:
                        pass
                    raise
                last_busy_exc = exc
                continue

        if self.psu is None:
            try:
                self.rm.close()
            except Exception:
                pass
            raise RuntimeError(
                "VISA resource busy on ASRL link. Close any other app using this COM port, "
                "wait 2-3 seconds, then retry."
            ) from last_busy_exc

        self.psu.timeout = 5000
        self.psu.query_delay = 0.05

        if self.address is not None:
            self._select_and_verify_address(self.address)
            self.idn = self._safe_query("*IDN?")

    @staticmethod
    def _blank_measurement(errors: str = "Transport failure") -> Dict[str, Any]:
        return {
            "voltage": None,
            "current": None,
            "output_state": None,
            "oper_cond": None,
            "oper_flags": "NA",
            "ques_cond": None,
            "fault_flags": "NA",
            "status_byte": None,
            "errors": errors,
        }


    @staticmethod
    def _parse_scpi_int(value: str) -> int:
        '''used to parse/convert the SCPI value from the response into an integer
        '''
        token = value.strip().strip('"')
        try:
            return int(token)
        except ValueError:
            return int(float(token))


    def _safe_query(self, command: str) -> Optional[str]:
        '''used to query the instrument and return the response. It is a defensive perimeter.
        '''
        try:
            return self.psu.query(command).strip()
        except pyvisa.errors.VisaIOError:
            return None

    def _safe_query_int(self, command: str) -> Optional[int]:
        '''used to query the instrument and return the response as an integer. It is a defensive perimeter.
        '''
        raw = self._safe_query(command)
        if raw is None:
            return None
        token = raw.strip().strip('"').split(",")[0] #.split(",")[0] → splits on commas and keeps the first field. And also, token and raw are strings.
        try:
            return int(token)
        except ValueError:
            try:
                return int(float(token))
            except ValueError:
                return None



    def _safe_write(self, command: str) -> bool:
        '''used to write a command to the instrument. It is a defensive perimeter.
        '''
        try:
            self.psu.write(command)
            return True
        except pyvisa.errors.VisaIOError:
            return False



    def _select_and_verify_address(
        self,
        address: int,
        force_reselect: bool = False,
        allow_retry: bool = True,
    ) -> None:
        '''used to select and verify the address of the instrument. It is a defensive perimeter.
        '''
        addr = int(address)
        if not force_reselect and self._selected_address_cache == addr:
            self.address = addr
            return

        try:
            self.psu.write(f"INSTrument:NSELect {addr}")
        except pyvisa.errors.VisaIOError as exc:
            self._selected_address_cache = None
            if allow_retry:
                self._select_and_verify_address(
                    address=addr,
                    force_reselect=True,
                    allow_retry=False,
                )
                return
            raise RuntimeError(
                f"Address select failed for {addr}: {exc}"
            ) from exc

        selected = None
        for wait_s in ADDRESS_VERIFY_BACKOFF_SECONDS:
            if wait_s > 0:
                time.sleep(wait_s)
            selected_raw = self._safe_query("INSTrument:NSELect?")
            if selected_raw is None:
                continue
            try:
                selected = self._parse_scpi_int(selected_raw)
            except (TypeError, ValueError):
                selected = None
                continue
            if selected == addr:
                self.address = addr
                self._selected_address_cache = addr
                return

        self._selected_address_cache = None
        if allow_retry:
            self._select_and_verify_address(
                address=addr,
                force_reselect=True,
                allow_retry=False,
            )
            return

        raise RuntimeError(
            f"Address handshake failed: requested {addr}, instrument reports {selected}."
        )



    def _drain_error_queue_selected(self, max_messages: int = 10) -> List[str]:
        ''' used to drain the error queue of the instrument. It is a defensive perimeter. max_messages is the maximum number of messages to drain.
        '''
        messages: List[str] = []
        for _ in range(max_messages):
            response = self._safe_query("SYST:ERR?")
            if response is None:
                messages.append(SYST_ERR_TIMEOUT)
                break
            if response.lstrip().startswith("0"):
                break
            messages.append(response)
        return messages



    def discover_supplies(
        self,
        candidate_addresses: Iterable[int],
        discovery_timeout_ms: int = 250,
        ) -> List[Dict[str, Any]]:
        '''used to discover the supplies on the instrument. candidate_addresses is the list of addresses to discover. candidate addresses is used in __main__ to pass the addresses of the supplies to discover. 
        
        has 2 memory registers: seen and found. seen is used to keep track of the addresses that have been seen. found is used to keep track of the addresses that have been found. 
        --Seen is a set of addresses that have been seen. 
        --Found is a list of dictionaries with the address and the IDN of the supply.
        THIS IS THE MOST IMPORTANT PART OF THE CODE.
        -----> found is the MAIN LIST of the entire program. 

                    [1, 2]      (candidate_addresses in __main__)    ← raw integers, no keys
                ↓  discover_supplies()
            [{"address": 1, "idn": "..."},  ← integers wrapped in dicts, key "address" invented HERE
            {"address": 2, "idn": "..."}]
                ↓  assigned to supplies
            supplies[0]["address"] → 1      ← retrieving what was stored
            supplies[1]["address"] → 2

        original_timeout saves the PSU's current/default timeout before discovery begins.
        discovery_timeout_ms is a temporary, shorter timeout used during scanning so missing addresses return quickly.
        The code applies discovery_timeout_ms to self.psu.timeout for the discovery loop, then restores original_timeout in the finally block, so the instrument always returns to its prior system timeout even if an error happens.
        '''
        found: List[Dict[str, Any]] = []
        seen = set()
        # NOTE: found is local to discover_supplies, so you cannot directly use found outside that method.
        # Inside method: valid.
        # Outside method: referencing found directly gives a NameError (not ValueError), because it’s out of scope.
        # You can still use its data elsewhere through the return value (e.g., supplies = tdk.discover_supplies(candidate_addresses))

        original_timeout = self.psu.timeout
        self.psu.timeout = discovery_timeout_ms
        try:
            for address in candidate_addresses:
                addr = int(address)
                if addr in seen:
                    continue
                seen.add(addr)
                try:
                    self._select_and_verify_address(addr)
                    idn = self._safe_query("*IDN?")
                    if idn:
                        found.append({"address": addr, "idn": idn}) #MOST IMPORTANT PART OF THE CODE. this is how "supplies" list is created from here on out.
                except (pyvisa.errors.VisaIOError, RuntimeError, ValueError):
                    continue
        finally:
            self.psu.timeout = original_timeout

        self._known_addresses = sorted({int(item["address"]) for item in found})
        return found




    def prepare_supplies_for_logging(self, supplies: List[Dict[str, Any]]) -> Dict[int, List[str]]:
        '''
        used to prepare the supplies for logging. supplies is the list of addresses to prepare.
        NOW!!
        ------> at this point, supplies is an empty list. But, in line 431, supplies is actually given data as: supplies = tdk.discover_supplies(candidate_addresses)
        ------> candidate_addresses is the list of addresses to discover. THIS COMES FROM discover_supplies method. THIS IS THE LIST OF ADDRESSES THAT HAVE BEEN DISCOVERED.

        ---> setup_errors is a dictionary with the address of the supply and the list of errors.

        WHAT THIS METHOD DOES: It goes through each supply in the list, selects the supply, writes *CLS, drains the error queue, and returns the setup errors.
        '''
        setup_errors: Dict[int, List[str]] = {}
        for supply in supplies:
            addr = int(supply["address"])
            self._select_and_verify_address(addr)
            if not self._safe_write("SYST:REM"):
                setup_errors.setdefault(addr, []).append("Timeout while sending SYST:REM")
                continue
            if not self._safe_write("*CLS"):
                setup_errors[addr] = ["Timeout while sending *CLS"]
                continue
            time.sleep(TDK_SETUP_POST_CLEAR_DELAY_SECONDS)
            drained = self._drain_error_queue_selected()
            if drained:
                non_timeout = [msg for msg in drained if msg != SYST_ERR_TIMEOUT]
                if non_timeout:
                    setup_errors.setdefault(addr, []).extend(non_timeout)

        if supplies:
            self._known_addresses = sorted({int(item["address"]) for item in supplies})
        return setup_errors



    def measure_supply(
        self,
        address: int,
        full_diagnostics: bool = True,
    ) -> Dict[str, Any]:
        addr = int(address)
        empty = self._blank_measurement(errors="Transport failure")
        cached = self._last_measurement_cache.get(
            addr,
            self._blank_measurement(errors=""),
        )

        try:
            self._select_and_verify_address(addr)

            voltage = self._safe_query("MEASure:VOLTage?")
            current = self._safe_query("MEASure:CURRent?")
            output_state_raw = self._safe_query("OUTPut:STATe?")
            output_state = {"0": "OFF", "1": "ON"}.get(
                (output_state_raw or "").strip(),
                output_state_raw,
            )
            ques_cond = self._safe_query_int("STATus:QUEStionable:CONDition?")

            if full_diagnostics:
                oper_cond = self._safe_query_int("STATus:OPERation:CONDition?")
                status_byte = self._safe_query_int("*STB?")
                oper_flags = decode_bits(oper_cond, OPER_BITS)
            else:
                oper_cond = cached.get("oper_cond")
                status_byte = cached.get("status_byte")
                oper_flags = cached.get("oper_flags") or "NA"

            needs_error_drain = (
                full_diagnostics
                or voltage is None
                or current is None
                or output_state_raw is None
                or (ques_cond is not None and ques_cond != 0)
                or (status_byte is not None and (int(status_byte) & 0b100) != 0)
            )
            errors = self._drain_error_queue_selected() if needs_error_drain else []

            result = {
                "voltage": voltage,
                "current": current,
                "output_state": output_state,
                "oper_cond": oper_cond,
                "oper_flags": oper_flags,
                "ques_cond": ques_cond,
                "fault_flags": decode_bits(ques_cond, FAULT_BITS),
                "status_byte": status_byte,
                "errors": " | ".join(errors),
            }
            self._last_measurement_cache[addr] = dict(result)
            return result
        except (pyvisa.errors.VisaIOError, RuntimeError):
            if addr in self._last_measurement_cache:
                stale = dict(self._last_measurement_cache[addr])
                stale["errors"] = "Transport failure"
                return stale
            return empty

    def measure_all_supplies(
        self,
        supplies: List[Dict[str, Any]],
        timestamp: Optional[float] = None,
        full_diagnostics: bool = True,
        control_request_event: Optional[Any] = None,
    ) -> Dict[str, Any]:
        row: Dict[str, Any] = {"timestamp": time.time() if timestamp is None else timestamp}
        for index, supply in enumerate(supplies):
            addr = int(supply["address"])
            if (
                control_request_event is not None
                and control_request_event.is_set()
                and index > 0
            ):
                data = dict(
                    self._last_measurement_cache.get(
                        addr,
                        self._blank_measurement(errors=""),
                    )
                )
            else:
                data = self.measure_supply(addr, full_diagnostics=full_diagnostics)

            row[f"ps_{addr}_voltage"] = data["voltage"]
            row[f"ps_{addr}_current"] = data["current"]
            row[f"ps_{addr}_output_state"] = data["output_state"]
            row[f"ps_{addr}_oper_cond"] = data["oper_cond"]
            row[f"ps_{addr}_oper_flags"] = data["oper_flags"]
            row[f"ps_{addr}_ques_cond"] = data["ques_cond"]
            row[f"ps_{addr}_fault_flags"] = data["fault_flags"]
            row[f"ps_{addr}_status_byte"] = data["status_byte"]
            row[f"ps_{addr}_errors"] = data["errors"]
        return row

    def set_voltage(self, address: int, voltage: float) -> None:
        self._select_and_verify_address(address)
        self.psu.write(f"SOURce:VOLTage:LEVel {voltage}")

    def set_current(self, address: int, current: float) -> None:
        self._select_and_verify_address(address)
        self.psu.write(f"SOURce:CURRent:LEVel {current}")

    @staticmethod
    def _format_sweep_values(values):
        return ",".join(f"{float(value):.9g}" for value in values)

    def abort_program(self, address=None):
        if address is not None:
            self._select_and_verify_address(address)
        self.psu.write("ABORt")
        self.psu.write("INITiate:CONTinuous OFF")

    def configure_list_sweep(
        self,
        address,
        points,
        repeat_count=1,
        step_mode="AUTO",
    ):
        rows = normalize_sweep_points(points, mode="list")
        if len(rows) > SWEEP_NATIVE_MAX_POINTS:
            raise ValueError(f"Native LIST mode supports up to {SWEEP_NATIVE_MAX_POINTS} points.")

        voltage_values = _non_null_values(rows, "voltage")
        current_values = _non_null_values(rows, "current")
        dwell_values = [float(row["duration_s"]) for row in rows]
        if voltage_values and len(voltage_values) != len(rows):
            raise ValueError("Native LIST voltage rows cannot contain blanks.")
        if current_values and len(current_values) != len(rows):
            raise ValueError("Native LIST current rows cannot contain blanks.")
        self._select_and_verify_address(address)
        self.abort_program()
        self.psu.write("TRIGger:SOURce BUS")
        self.psu.write("VOLTage:MODE NONE")
        self.psu.write("CURRent:MODE NONE")
        if voltage_values:
            self.psu.write("VOLTage:MODE LIST")
            self.psu.write(f"LIST:VOLTage {self._format_sweep_values(voltage_values)}")
        if current_values:
            self.psu.write("CURRent:MODE LIST")
            self.psu.write(f"LIST:CURRent {self._format_sweep_values(current_values)}")
        self.psu.write(f"LIST:DWELl {self._format_sweep_values(dwell_values)}")
        self.psu.write(f"LIST:COUNt {int(repeat_count)}")
        self.psu.write(f"LIST:STEP {_normalize_sweep_step_mode(step_mode)}")

    def configure_wave_sweep(
        self,
        address,
        points,
        repeat_count=1,
        step_mode="AUTO",
    ):
        rows = normalize_sweep_points(points, mode="wave")
        if len(rows) > SWEEP_NATIVE_MAX_POINTS:
            raise ValueError(f"Native WAVE mode supports up to {SWEEP_NATIVE_MAX_POINTS} points.")

        wave_axis = _select_wave_axis(rows)
        voltage_values = _non_null_values(rows, "voltage")
        current_values = _non_null_values(rows, "current")
        time_values = [float(row["duration_s"]) for row in rows]
        if len(_non_null_values(rows, wave_axis)) != len(rows):
            raise ValueError(f"Native WAVE {wave_axis} rows cannot contain blanks.")
        self._select_and_verify_address(address)
        self.abort_program()
        self.psu.write("TRIGger:SOURce BUS")
        self.psu.write("VOLTage:MODE NONE")
        self.psu.write("CURRent:MODE NONE")
        if wave_axis == "voltage":
            if current_values:
                self.set_current(address=address, current=current_values[0])
            self._select_and_verify_address(address)
            self.psu.write("VOLTage:MODE WAVE")
            self.psu.write(f"WAVE:VOLTage {self._format_sweep_values(voltage_values)}")
        else:
            if voltage_values:
                self.set_voltage(address=address, voltage=voltage_values[0])
            self._select_and_verify_address(address)
            self.psu.write("CURRent:MODE WAVE")
            self.psu.write(f"WAVE:CURRent {self._format_sweep_values(current_values)}")
        self.psu.write(f"WAVE:TIME {self._format_sweep_values(time_values)}")
        self.psu.write(f"WAVE:COUNt {int(repeat_count)}")
        self.psu.write(f"WAVE:STEP {_normalize_sweep_step_mode(step_mode)}")

    def configure_fix_trigger(self, address, point):
        rows = normalize_sweep_points([point], mode="fix")
        selected = rows[0]
        self._select_and_verify_address(address)
        self.abort_program()
        self.psu.write("TRIGger:SOURce BUS")
        self.psu.write("VOLTage:MODE NONE")
        self.psu.write("CURRent:MODE NONE")
        if selected.get("voltage") is not None:
            self.psu.write("VOLTage:MODE FIX")
            self.psu.write(f"VOLTage:TRIGger {float(selected['voltage']):.9g}")
        if selected.get("current") is not None:
            self.psu.write("CURRent:MODE FIX")
            self.psu.write(f"CURRent:TRIGger {float(selected['current']):.9g}")

    def trigger_program(self, address):
        self._select_and_verify_address(address)
        self.psu.write("INITiate")
        self.psu.write("*TRG")

    def _query_first_float(self, commands: Iterable[str]) -> Optional[float]:
        for command in commands:
            value = parse_numeric_value(self._safe_query(command))
            if value is not None:
                return value
        return None

    @staticmethod
    def _fallback_limits_for_address(address: int) -> Dict[str, float]:
        return HV_FALLBACK_LIMITS_BY_ADDRESS.get(
            int(address),
            HV_DEFAULT_FALLBACK_LIMITS,
        )

    def get_voltage_limit_max(self, address: int) -> float:
        self._select_and_verify_address(address)
        query_candidates = [
            "SOURce:VOLTage:LEVel? MAX",
            "SOURce:VOLTage:LEVel:IMMediate:AMPLitude? MAX",
            "SOURce:VOLTage:LEVel:IMMediate? MAX",
        ]
        queried = self._query_first_float(query_candidates)
        if queried is not None and queried > 0:
            return queried
        return self._fallback_limits_for_address(address)["voltage"]

    def get_current_limit_max(self, address: int) -> float:
        self._select_and_verify_address(address)
        query_candidates = [
            "SOURce:CURRent:LEVel? MAX",
            "SOURce:CURRent:LEVel:IMMediate:AMPLitude? MAX",
            "SOURce:CURRent:LEVel:IMMediate? MAX",
        ]
        queried = self._query_first_float(query_candidates)
        if queried is not None and queried > 0:
            return queried
        return self._fallback_limits_for_address(address)["current"]

    def get_programmed_voltage_setpoint(self, address: int) -> Optional[float]:
        self._select_and_verify_address(address)
        query_candidates = [
            "SOURce:VOLTage:LEVel?",
            "SOURce:VOLTage:LEVel:IMMediate:AMPLitude?",
            "SOURce:VOLTage:LEVel:IMMediate?",
        ]
        return self._query_first_float(query_candidates)

    def get_programmed_current_setpoint(self, address: int) -> Optional[float]:
        self._select_and_verify_address(address)
        query_candidates = [
            "SOURce:CURRent:LEVel?",
            "SOURce:CURRent:LEVel:IMMediate:AMPLitude?",
            "SOURce:CURRent:LEVel:IMMediate?",
        ]
        return self._query_first_float(query_candidates)

    def turn_on(self, address: int) -> None:
        self._select_and_verify_address(address)
        self.psu.write("OUTPut:STATe ON")

    def turn_off(self, address: int) -> None:
        self._select_and_verify_address(address)
        self.psu.write("OUTPut:STATe OFF")

    def global_on(self) -> None:
        self.psu.write("GLOBal:OUTPut:STATe ON")
        time.sleep(0.02)

    def global_off(self) -> None:
        self.psu.write("GLOBal:OUTPut:STATe OFF")
        time.sleep(0.02)

    def close(self) -> None:
        """Release session resources and return known supplies to local mode."""
        known_addresses = list(self._known_addresses)
        for addr in sorted(set(known_addresses)):
            try:
                self._select_and_verify_address(
                    address=addr,
                    force_reselect=True,
                    allow_retry=False,
                )
                self._safe_write("SYST:LOC")
            except Exception:
                pass

        try:
            self.psu.close()
        except Exception:
            pass
        try:
            self.rm.close()
        except Exception:
            pass


def build_fieldnames(supplies: List[Dict[str, Any]]) -> List[str]:
    fieldnames = ["timestamp"]
    for supply in supplies:
        addr = int(supply["address"])
        fieldnames.extend(
            [
                f"ps_{addr}_voltage",
                f"ps_{addr}_current",
                f"ps_{addr}_output_state",
                f"ps_{addr}_oper_cond",
                f"ps_{addr}_oper_flags",
                f"ps_{addr}_ques_cond",
                f"ps_{addr}_fault_flags", #Basically, decoded ques_cond into a string of fault flags.
                f"ps_{addr}_status_byte",
                f"ps_{addr}_errors",
            ]
        )
    return fieldnames


def print_live_row(timestamp: float, row: Dict[str, Any], supplies: List[Dict[str, Any]]) -> None: 
    '''used to print the live row of the supplies. from "row" dictionary
    now, this method is getting realtime values from measure_all_supplies method, the data structure having this data is "row"!!!! THIS IS THE MOST IMPORTANT PART OF THE CODE.
    ----> row = tdk.measure_all_supplies(supplies, timestamp=t_now) → this is the row that is printed in the terminal.

    go to this section:
        print_live_row(t_now, row, supplies)
        writer.writerow(row) → this is the line that writes the row to the csv file.
    '''
    parts: List[str] = []
    for supply in supplies:
        addr = int(supply["address"])
        voltage = fmt_value(row.get(f"ps_{addr}_voltage"))
        current = fmt_value(row.get(f"ps_{addr}_current"))
        output_state = row.get(f"ps_{addr}_output_state") or "NA"
        parts.append(f"PS{addr}: V={voltage} I={current} OUT={output_state}")

    print(f"{timestamp:.3f}: " + " | ".join(parts))

    for supply in supplies:
        addr = int(supply["address"])
        fault_flags = row.get(f"ps_{addr}_fault_flags") or "NA"
        error_text = row.get(f"ps_{addr}_errors") or ""

        if fault_flags not in ("NONE", "NA"):
            ques_cond = row.get(f"ps_{addr}_ques_cond")
            print(f"WARNING [PS{addr}] ques_cond={ques_cond} flags={fault_flags}")
        if error_text:
            print(f"ERROR   [PS{addr}] {error_text}")


def summarize_programmed_limits(programmed_limits: Dict[int, Dict[str, Any]]) -> str:
    '''
    This method is used to summarize the programmed limits for the supplies.
    It is used to print the programmed limits for the supplies in the terminal.
    '''
    parts: List[str] = []
    for address in sorted(programmed_limits):
        limits = programmed_limits[address]
        parts.append(
            (
                f"PS{address} "
                f"target(V={fmt_value(limits.get('voltage'), digits=3)},"
                f"I={fmt_value(limits.get('current'), digits=6)}) "
                f"readback(V={fmt_value(limits.get('readback_voltage'), digits=3)},"
                f"I={fmt_value(limits.get('readback_current'), digits=6)})"
            )
        )
    return " | ".join(parts) if parts else "No programmed limits."


def program_hv_max_for_addresses(
    tdk: TDKLambda,
    addresses: Iterable[int],
    limit_source: str = "instrument_query",
) -> Dict[int, Dict[str, Any]]:
    source = limit_source.strip().lower()
    if source not in {"instrument_query", "fallback"}:
        raise ValueError(
            f"Unsupported hv limit source '{limit_source}'. Use 'instrument_query' or 'fallback'."
        )

    programmed: Dict[int, Dict[str, Any]] = {}
    seen = set()
    for raw_address in addresses:
        address = int(raw_address)
        if address in seen:
            continue
        seen.add(address)

        if source == "instrument_query":
            voltage_max = float(tdk.get_voltage_limit_max(address))
            current_max = float(tdk.get_current_limit_max(address))
        else:
            fallback = TDKLambda._fallback_limits_for_address(address)
            voltage_max = float(fallback["voltage"])
            current_max = float(fallback["current"])

        tdk.set_voltage(address=address, voltage=voltage_max)
        tdk.set_current(address=address, current=current_max)
        readback_voltage = tdk.get_programmed_voltage_setpoint(address)
        readback_current = tdk.get_programmed_current_setpoint(address)

        if source == "fallback":
            if readback_voltage is None or readback_current is None:
                raise RuntimeError(
                    f"HV verify failed for PS{address}: setpoint readback unavailable."
                )
            if readback_voltage > (voltage_max + HV_VERIFY_MAX_OVERSHOOT_V):
                raise RuntimeError(
                    f"HV verify failed for PS{address}: readback voltage "
                    f"{readback_voltage:.3f} V exceeds target {voltage_max:.3f} V."
                )
            if readback_current > (current_max + HV_VERIFY_MAX_OVERSHOOT_A):
                raise RuntimeError(
                    f"HV verify failed for PS{address}: readback current "
                    f"{readback_current:.6f} A exceeds target {current_max:.6f} A."
                )

        programmed[address] = {
            "voltage": voltage_max,
            "current": current_max,
            "readback_voltage": readback_voltage,
            "readback_current": readback_current,
        }

    return programmed


def _stop_requested(stop_event: Optional[Any]) -> bool:
    return bool(stop_event is not None and stop_event.is_set())


def _sleep_with_stop(duration_s: float, stop_event: Optional[Any]) -> None:
    end_time = time.time() + max(duration_s, 0.0)
    while time.time() < end_time:
        if _stop_requested(stop_event):
            break
        time.sleep(min(0.05, end_time - time.time()))


def _run_with_io_lock(io_lock: Optional[Any], operation: Any) -> Any:
    if io_lock is None:
        return operation()
    with io_lock:
        return operation()


def _mark_first_post_control_measurement(
    control_timing: Optional[Dict[str, Any]],
    control_timing_lock: Optional[Any],
    measurement_ts: float,
) -> None:
    if control_timing is None or control_timing_lock is None:
        return

    with control_timing_lock:
        marker_id = control_timing.get("marker_id")
        seen_marker_id = control_timing.get("seen_marker_id")
        dispatch_ts = control_timing.get("dispatch_ts")
        label = control_timing.get("label") or "CONTROL"
        waiting = bool(control_timing.get("awaiting_measurement"))
        if (
            not waiting
            or marker_id is None
            or marker_id == seen_marker_id
            or dispatch_ts is None
        ):
            return

        delta_ms = max(0.0, (measurement_ts - float(dispatch_ts)) * 1000.0)
        control_timing["seen_marker_id"] = marker_id
        control_timing["awaiting_measurement"] = False
        control_timing["first_measure_ts"] = measurement_ts
        control_timing["delta_ms"] = delta_ms

    print(
        f"CONTROL_LATENCY [{label}] "
        f"dispatch={float(dispatch_ts):.3f} "
        f"first_measure={measurement_ts:.3f} "
        f"delta_ms={delta_ms:.1f}"
    )


def _normalize_manual_overrides(
    manual_overrides: Optional[Dict[int, Dict[str, float]]],
) -> Dict[int, Dict[str, float]]:
    '''
    This function takes a dictionary of manual overrides for power supply settings (voltage/current per address), validates and normalizes its values, and returns a new dictionary with integer addresses as keys and only those voltage/current values that are present and non-None, explicitly cast to float. Invalid entries or formats are skipped. This ensures settings are clean, numeric, and usable.
    '''
    if not manual_overrides:
        return {}

    normalized: Dict[int, Dict[str, float]] = {}
    for address_key, setting_map in manual_overrides.items():
        try:
            address = int(address_key)
        except (TypeError, ValueError):
            continue

        if not isinstance(setting_map, dict):
            continue

        selected: Dict[str, float] = {}
        if "voltage" in setting_map and setting_map["voltage"] is not None:
            selected["voltage"] = float(setting_map["voltage"])
        if "current" in setting_map and setting_map["current"] is not None:
            selected["current"] = float(setting_map["current"])

        if selected:
            normalized[address] = selected

    return normalized


def _persisted_trip_condition(
    condition_since: Dict[Tuple[int, str], float],
    key: Tuple[int, str],
    is_active,
    timestamp,
    reason_text,
) -> Optional[str]:
    """Trip only after a condition stays active for the persistence window."""
    if not is_active:
        condition_since.pop(key, None) #pop is used to delete the key from condition_since when the condition is no longer active, so old/stale entries are cleared from the tracking dictionary. "None" is the value to return if the key is not found.
        return None

    since = condition_since.get(key)
    if since is None:
        condition_since[key] = float(timestamp)
        return None

    if (float(timestamp) - float(since)) < HV_TRIP_PERSIST_SECONDS:
        return None
    return str(reason_text)


def _evaluate_hv_trip(
    row: Dict[str, Any],
    supplies: List[Dict[str, Any]],
    timestamp: float,
    
    voltage_windows: Dict[int, List[Tuple[float, float]]],
    condition_since: Dict[Tuple[int, str], float],
) -> Optional[str]:
    """Evaluate HV safeguards using persistence and rolling-average checks.

    `condition_since` tracks (address, condition_name) -> first-seen timestamp,
    so short transients do not trigger immediate trips.
    """
    for supply in supplies:
        address = int(supply["address"])
        voltage_key = f"ps_{address}_voltage"
        current_key = f"ps_{address}_current"

        voltage = parse_numeric_value(row.get(voltage_key))
        current = parse_numeric_value(row.get(current_key))

        reason = _persisted_trip_condition(
            condition_since=condition_since,
            key=(address, "voltage_unreadable"),
            is_active=(voltage is None),
            timestamp=timestamp,
            reason_text=(
                f"HV trip: PS{address} voltage unreadable "
                f"for >= {HV_TRIP_PERSIST_SECONDS:.1f}s."
            ),
        )
        if reason:
            return reason

        reason = _persisted_trip_condition(
            condition_since=condition_since,
            key=(address, "current_unreadable"),
            is_active=(current is None),
            timestamp=timestamp,
            reason_text=(
                f"HV trip: PS{address} current unreadable "
                f"for >= {HV_TRIP_PERSIST_SECONDS:.1f}s."
            ),
        )
        if reason:
            return reason

        #This is for high voltage trip, but we are not using it for now. AS, IT IS REDUNDANT AGAINST, if we use a different gas, which might need higher V draw for Ionization, or higher breakdown potential.VERY IMPORTANT. DO NOT REMOVE THIS COMMENT. It is commented out because we are not using it for now.

        # high_voltage_active = bool(
        #     voltage is not None and voltage >= HV_TRIP1_MAXV
        # )
        # reason = _persisted_trip_condition(
        #     condition_since=condition_since,
        #     key=(address, "voltage_high"),
        #     is_active=high_voltage_active,
        #     timestamp=timestamp,
        #     reason_text=(
        #         f"HV trip: PS{address} voltage {float(voltage):.3f} V "
        #         f">= {HV_TRIP1_MAXV:.3f} V "
        #         f"for >= {HV_TRIP_PERSIST_SECONDS:.1f}s."
        #         if high_voltage_active
        #         else ""
        #     ),
        # )
        # if reason:
        #     return reason

        low_current_active = bool(
            current is not None and current < HV_TRIP2_MIN_I
        )
        reason = _persisted_trip_condition(
            condition_since=condition_since,
            key=(address, "current_low"),
            is_active=low_current_active,
            timestamp=timestamp,
            reason_text=(
                f"HV trip: PS{address} current {float(current):.3f} A "
                f"< {HV_TRIP2_MIN_I:.3f} A "
                f"for >= {HV_TRIP_PERSIST_SECONDS:.1f}s."
                if low_current_active
                else ""
            ),
        )
        if reason:
            return reason

        # window = voltage_windows.setdefault(address, [])
        # if voltage is not None:
        #     window.append((timestamp, voltage))
        # cutoff = timestamp - HV_AVG_WINDOW_SECONDS
        # while window and window[0][0] < cutoff:
        #     window.pop(0)

        # avg_voltage = None
        # if voltage is not None and window:
        #     avg_voltage = sum(item[1] for item in window) / len(window)

        # avg_window_label = (
        #     f"{int(HV_AVG_WINDOW_SECONDS)}s"
        #     if float(HV_AVG_WINDOW_SECONDS).is_integer()
        #     else f"{HV_AVG_WINDOW_SECONDS:.1f}s"
        # )
        # avg_voltage_low_active = bool(
        #     avg_voltage is not None and avg_voltage < HV_TRIP3_AVGV
        # )
        # reason = _persisted_trip_condition(
        #     condition_since=condition_since,
        #     key=(address, "avg_voltage_low"),
        #     is_active=avg_voltage_low_active,
        #     timestamp=timestamp,
        #     reason_text=(
        #         f"HV trip: PS{address} {avg_window_label} avg voltage {float(avg_voltage):.3f} V "
        #         f"< {HV_TRIP3_AVGV:.3f} V "
        #         f"for >= {HV_TRIP_PERSIST_SECONDS:.1f}s."
        #         if avg_voltage_low_active
        #         else ""
        #     ),
        # )
        # if reason:
        #     return reason

    return None


def _evaluate_runtime_fault(
    row: Dict[str, Any],
    supplies: List[Dict[str, Any]],
) -> Optional[str]:
    for supply in supplies:
        address = int(supply["address"])

        ques_cond = parse_numeric_value(row.get(f"ps_{address}_ques_cond"))
        if ques_cond is not None:
            try:
                if int(ques_cond) != 0:
                    return f"Runtime fault: PS{address} ques_cond={int(ques_cond)}."
            except (TypeError, ValueError, OverflowError):
                return f"Runtime fault: PS{address} ques_cond unreadable."

        fault_flags = str(row.get(f"ps_{address}_fault_flags") or "").strip()
        if fault_flags and fault_flags not in {"NONE", "NA"}:
            return f"Runtime fault: PS{address} flags={fault_flags}."

        error_text = str(row.get(f"ps_{address}_errors") or "").strip()
        if error_text:
            return f"Runtime error: PS{address} {error_text}"

    return None


def _supplies_for_addresses(
    supplies: List[Dict[str, Any]],
    addresses: List[int],
) -> List[Dict[str, Any]]:
    selected = {int(address) for address in addresses}
    return [supply for supply in supplies if int(supply["address"]) in selected]


def _compute_hv_retry_cooldown(trip_history: List[float], now_ts):
    """Return adaptive retry cooldown from recent trip density.

    Uses HV_RETRY_* constants to prune old trip timestamps, apply escalation
    levels, and clamp the final cooldown to a hard maximum.
    """
    cutoff = now_ts - HV_RETRY_GUARD_WINDOW_SECONDS
    while trip_history and trip_history[0] < cutoff:
        trip_history.pop(0)

    cooldown = HV_RETRY_BASE_COOLDOWN_SECONDS
    for minimum_count, escalated_cooldown in HV_RETRY_ESCALATION_LEVELS:
        if len(trip_history) >= int(minimum_count):
            cooldown = max(cooldown, float(escalated_cooldown))
    return min(float(cooldown), HV_RETRY_MAX_COOLDOWN_SECONDS)


def _legacy_phase3_selection(voltages: Dict[int, float]):
    """Preserve legacy behavior: prefer PS2 ON and PS1 OFF."""
    if 1 in voltages and 2 in voltages:
        return 2, 1
    ordered_addresses = sorted(voltages)
    keep_address = int(ordered_addresses[-1])
    off_address = int(ordered_addresses[0]) if len(ordered_addresses) > 1 else None
    return keep_address, off_address


def _pick_phase3_supply(
    row: Dict[str, Any],
    active_addresses: List[int],
):
    """Select the supply to keep ON during phase-3 transition."""
    voltages: Dict[int, float] = {}
    for address in active_addresses:
        voltage = parse_numeric_value(row.get(f"ps_{int(address)}_voltage"))
        if voltage is None:
            return None, None
        voltages[int(address)] = float(voltage)

    if not voltages:
        return None, None

    if len(voltages) == 1:
        selected = next(iter(voltages))
        return selected, None

    selected_policy = str(HV_PHASE3_SHUTOFF_POLICY).strip().lower()
    if selected_policy not in HV_PHASE3_ALLOWED_POLICIES:
        selected_policy = "always_ps1"

    if selected_policy == "always_ps1":
        return _legacy_phase3_selection(voltages)

    ranked = sorted(
        voltages.items(),
        key=lambda item: item[1],
        reverse=True,
    )
    high_address, high_voltage = ranked[0]
    low_address, low_voltage = ranked[-1]

    if abs(high_voltage - low_voltage) <= HV_PHASE3_TIE_DELTA_V:
        return _legacy_phase3_selection(voltages)

    return int(high_address), int(low_address)


def _safe_status_emit(status_callback, message):
    if status_callback is None:
        return
    try:
        status_callback(message)
    except Exception:
        pass


def _safe_hv_shutdown(
    logger,
    hv_addresses: List[int],
    io_lock,
):
    """Run best-effort global and per-address OFF commands."""
    try:
        _run_with_io_lock(io_lock, logger.global_off)
    except Exception:
        pass
    for address in sorted(set(hv_addresses)):
        try:
            _run_with_io_lock(
                io_lock,
                lambda address=address: logger.turn_off(address=address),
            )
        except Exception:
            pass


def _emit_sweep_status(status_callback, message):
    if status_callback is None:
        return
    try:
        status_callback(message)
    except Exception:
        pass


def _estimate_sweep_duration(points, repeat_count):
    grouped = _group_sweep_points_by_address(points)
    longest = 0.0
    for rows in grouped.values():
        longest = max(longest, sum(float(row["duration_s"]) for row in rows))
    return longest * max(int(repeat_count), 1)


def _evaluate_sweep_fault(row, supplies, hv_mode, voltage_windows, condition_since):
    reason = None
    if hv_mode:
        reason = _evaluate_hv_trip(
            row=row,
            supplies=supplies,
            timestamp=float(row.get("timestamp") or time.time()),
            voltage_windows=voltage_windows,
            condition_since=condition_since,
        )
    if reason is None:
        reason = _evaluate_runtime_fault(row=row, supplies=supplies)
    return reason


def _monitor_sweep_window(
    tdk,
    supplies,
    duration_s,
    stop_event,
    hv_mode,
    io_lock,
    status_callback,
    label,
):
    voltage_windows: Dict[int, List[Tuple[float, float]]] = {}
    condition_since: Dict[Tuple[int, str], float] = {}
    for supply in supplies:
        voltage_windows[int(supply["address"])] = []

    end_time = time.time() + max(float(duration_s), 0.0)
    while time.time() < end_time:
        if _stop_requested(stop_event):
            return
        row = _run_with_io_lock(
            io_lock,
            lambda: tdk.measure_all_supplies(
                supplies=supplies,
                timestamp=time.time(),
                full_diagnostics=True,
                control_request_event=None,
            ),
        )
        reason = _evaluate_sweep_fault(
            row=row,
            supplies=supplies,
            hv_mode=hv_mode,
            voltage_windows=voltage_windows,
            condition_since=condition_since,
        )
        if reason:
            raise RuntimeError(reason)
        _emit_sweep_status(status_callback, f"{label}: monitoring sweep.")
        _sleep_with_stop(min(SWEEP_POLL_SECONDS, end_time - time.time()), stop_event)


def _validate_sweep_against_limits(tdk, points, io_lock):
    grouped = _group_sweep_points_by_address(points)
    for address, rows in grouped.items():
        voltage_limit = _run_with_io_lock(
            io_lock,
            lambda address=address: tdk.get_voltage_limit_max(address),
        )
        current_limit = _run_with_io_lock(
            io_lock,
            lambda address=address: tdk.get_current_limit_max(address),
        )
        for index, row in enumerate(rows, start=1):
            voltage = row.get("voltage")
            current = row.get("current")
            if voltage is not None and float(voltage) > float(voltage_limit):
                raise ValueError(
                    f"PS{address} sweep point {index} voltage {float(voltage):.3f} V "
                    f"exceeds limit {float(voltage_limit):.3f} V."
                )
            if current is not None and float(current) > float(current_limit):
                raise ValueError(
                    f"PS{address} sweep point {index} current {float(current):.6f} A "
                    f"exceeds limit {float(current_limit):.6f} A."
                )


def _configure_native_sweep(tdk, mode, grouped, repeat_count, step_mode):
    for address, rows in sorted(grouped.items()):
        if mode == "list":
            tdk.configure_list_sweep(
                address=address,
                points=rows,
                repeat_count=repeat_count,
                step_mode=step_mode,
            )
        elif mode == "wave":
            tdk.configure_wave_sweep(
                address=address,
                points=rows,
                repeat_count=repeat_count,
                step_mode=step_mode,
            )
        else:
            tdk.configure_fix_trigger(address=address, point=rows[0])


def _safe_abort_sweep(tdk, addresses, io_lock):
    for address in sorted(set(addresses)):
        try:
            _run_with_io_lock(
                io_lock,
                lambda address=address: tdk.abort_program(address=address),
            )
        except Exception:
            pass
    try:
        _run_with_io_lock(io_lock, tdk.global_off)
    except Exception:
        pass
    for address in sorted(set(addresses)):
        try:
            _run_with_io_lock(
                io_lock,
                lambda address=address: tdk.turn_off(address=address),
            )
        except Exception:
            pass


def run_sweep_sequence(
    tdk,
    points,
    mode="list",
    repeat_count=1,
    step_mode="AUTO",
    stop_event=None,
    hv_mode=False,
    io_lock=None,
    status_callback=None,
    native_preferred=True,
    turn_outputs=True,
):
    mode_normalized = _normalize_sweep_mode(mode)
    step_mode_normalized = _normalize_sweep_step_mode(step_mode)
    repeat_count = int(repeat_count)
    if repeat_count < 1:
        raise ValueError("repeat_count must be >= 1.")

    normalized = normalize_sweep_points(points, mode=mode_normalized)
    _validate_sweep_against_limits(tdk=tdk, points=normalized, io_lock=io_lock)
    grouped = _group_sweep_points_by_address(normalized)
    addresses = sorted(grouped)
    supplies = [{"address": address, "idn": ""} for address in addresses]
    native_ready = (
        native_preferred
        and mode_normalized != "software"
        and sweep_native_possible(mode_normalized, normalized)
    )
    engine = "native" if native_ready else "software"

    try:
        if native_ready:
            _emit_sweep_status(status_callback, f"Configuring native {mode_normalized.upper()} sweep.")
            _run_with_io_lock(
                io_lock,
                lambda: _configure_native_sweep(
                    tdk=tdk,
                    mode=mode_normalized,
                    grouped=grouped,
                    repeat_count=repeat_count,
                    step_mode=step_mode_normalized,
                ),
            )
            if turn_outputs:
                for address in addresses:
                    _run_with_io_lock(
                        io_lock,
                        lambda address=address: tdk.turn_on(address=address),
                    )
                _run_with_io_lock(io_lock, tdk.global_on)
            for address in addresses:
                _run_with_io_lock(
                    io_lock,
                    lambda address=address: tdk.trigger_program(address=address),
                )
            monitor_duration = (
                max(SWEEP_POLL_SECONDS, _estimate_sweep_duration(normalized, repeat_count))
                if step_mode_normalized == "AUTO"
                else SWEEP_POLL_SECONDS
            )
            _monitor_sweep_window(
                tdk=tdk,
                supplies=supplies,
                duration_s=monitor_duration + SWEEP_POLL_SECONDS,
                stop_event=stop_event,
                hv_mode=hv_mode,
                io_lock=io_lock,
                status_callback=status_callback,
                label=f"Native {mode_normalized.upper()}",
            )
        else:
            _emit_sweep_status(status_callback, "Running software sweep.")
            for repeat_index in range(repeat_count):
                for point_index, point in enumerate(normalized, start=1):
                    if _stop_requested(stop_event):
                        break
                    address = int(point["address"])

                    def apply_point(point=point, address=address):
                        if point.get("current") is not None:
                            tdk.set_current(address=address, current=point["current"])
                        if point.get("voltage") is not None:
                            tdk.set_voltage(address=address, voltage=point["voltage"])
                        if turn_outputs:
                            tdk.turn_on(address=address)

                    _run_with_io_lock(io_lock, apply_point)
                    if turn_outputs:
                        _run_with_io_lock(io_lock, tdk.global_on)
                    _emit_sweep_status(
                        status_callback,
                        (
                            f"Software sweep repeat {repeat_index + 1}/{repeat_count}, "
                            f"point {point_index}/{len(normalized)}."
                        ),
                    )
                    _monitor_sweep_window(
                        tdk=tdk,
                        supplies=supplies,
                        duration_s=float(point["duration_s"]),
                        stop_event=stop_event,
                        hv_mode=hv_mode,
                        io_lock=io_lock,
                        status_callback=None,
                        label="Software",
                    )
        return {
            "engine": engine,
            "mode": mode_normalized,
            "addresses": addresses,
            "stopped": _stop_requested(stop_event),
        }
    finally:
        _safe_abort_sweep(tdk=tdk, addresses=addresses, io_lock=io_lock)


def run_logging_session(
    csv_path: str = csv_filename,
    port: str = "ASRL4::INSTR",
    candidate_addresses: Optional[Iterable[int]] = None,
    sample_period_s: float = 0.1,
    stop_event: Optional[Any] = None,
    mode: str = "auto",
    manual_overrides: Optional[Dict[int, Dict[str, float]]] = None,
    hv_mode: bool = False,
    hv_armed: bool = False,
    hv_limit_source: str = "instrument_query",
    preferred_target_address: int = 2,
    print_rows: bool = True,
    tdk: Optional[TDKLambda] = None,
    io_lock: Optional[Any] = None,
    close_session: bool = True,
    full_diag_every_n: int = DEFAULT_FULL_DIAG_EVERY_N,
    hv_full_diag_every_n: int = HV_FULL_DIAG_EVERY_N,
    control_request_event: Optional[Any] = None,
    control_timing: Optional[Dict[str, Any]] = None,
    control_timing_lock: Optional[Any] = None,
    status_callback: Optional[Any] = None,
    start_outputs = True,
) -> Dict[str, Any]:
    """Run one scan/logging session with optional output control.

    In HV mode this executes the phased automation state machine.
    In non-HV modes it preserves existing auto/manual behavior.

    Key runtime variables:
    - hv_phase: active HV automation state.
    - hv_active_addresses: supplies currently considered ON by the state machine.
    - hv_trip_condition_since: persistence timers per trip condition.
    - hv_trip_history / hv_cooldown_until: adaptive retry scheduling state.
    """
    if sample_period_s < 0:
        raise ValueError("sample_period_s must be zero or greater.")
    if int(full_diag_every_n) < 1:
        raise ValueError("full_diag_every_n must be >= 1.")
    if int(hv_full_diag_every_n) < 1:
        raise ValueError("hv_full_diag_every_n must be >= 1.")

    mode_normalized = mode.strip().lower()
    if mode_normalized not in {"auto", "manual"}:
        raise ValueError(f"Unsupported mode '{mode}'. Use 'auto' or 'manual'.")
    hv_limit_source_normalized = hv_limit_source.strip().lower()
    if hv_limit_source_normalized not in {"instrument_query", "fallback"}:
        raise ValueError(
            f"Unsupported hv limit source '{hv_limit_source}'. "
            "Use 'instrument_query' or 'fallback'."
        )

    candidate_list = [1, 2] if candidate_addresses is None else [int(x) for x in candidate_addresses]
    normalized_overrides = _normalize_manual_overrides(manual_overrides)

    output_path = os.path.abspath(csv_path)
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    tdk_snapshot_path = os.path.join(PROJECT_ROOT, "tdk_snapshot.json")

    owns_session = tdk is None
    logger = TDKLambda(port=port, address=None) if owns_session else tdk #This is the TDKLambda object that is used to communicate with the TDK power supplies. FIRST CALLS THE CONSTRUCTOR OF THE TDKLambda CLASS.
    if logger is None:
        raise RuntimeError("TDK session unavailable.")

    target_address: Optional[int] = None
    supplies: List[Dict[str, Any]] = []
    started_addresses: List[int] = []
    hv_programmed_limits: Dict[int, Dict[str, Any]] = {}
    hv_confirmation_text = ""
    hv_voltage_windows: Dict[int, List[Tuple[float, float]]] = {}  # Per-PS rolling V history.
    hv_trip_condition_since: Dict[Tuple[int, str], float] = {}  # Condition persistence timers.
    hv_addresses: List[int] = []
    hv_active_addresses: List[int] = []  # Addresses actively monitored for HV trips.
    hv_phase = "idle"  # idle -> phase1/phase2/phase4 (phase3 is transition logic).
    hv_phase1_stable_since = None  # Start time of the no-trip phase1 settle window.
    hv_single_address = None  # PSU kept ON after phase3 higher-voltage selection.
    hv_trip_history: List[float] = []  # Recent trip timestamps used for backoff scaling.
    hv_cooldown_until = 0.0  # Absolute time when next auto-retry is allowed.
    hv_trip_count = 0
    hv_last_trip_reason = ""
    hv_last_trip_timestamp = None
    tripped = False
    trip_reason = ""
    trip_timestamp: Optional[float] = None
    hv_arm_consumed = False

    try:
        supplies = _run_with_io_lock(
            io_lock,
            lambda: logger.discover_supplies(candidate_list), #lambda here is a function (specifically, an anonymous function) that, when called, will execute logger.discover_supplies(candidate_list). It is passed to _run_with_io_lock to defer the execution until inside the function, and to allow it to be called with the lock held if needed.
        )
        print("supplies:", supplies)
        if not supplies:
            raise RuntimeError("No responding supplies discovered on this bus.")

        print("Discovered supplies:")
        for supply in supplies:
            print(f"Address {supply['address']}: {supply['idn']}")

        setup_errors = _run_with_io_lock(
            io_lock,
            lambda: logger.prepare_supplies_for_logging(supplies),
        )
        if setup_errors:
            merged = []
            for addr, errs in setup_errors.items():
                merged.append(f"PS{addr}: {' | '.join(errs)}")
            raise RuntimeError("TDK setup errors: " + " || ".join(merged))

        discovered_addresses = {int(s["address"]) for s in supplies}
        if hv_mode and start_outputs and not hv_armed:
            raise RuntimeError("HV mode requires ARM HV before any ON command.")

        hv_addresses = sorted(discovered_addresses)
        if mode_normalized == "auto":
            target_address = (
                preferred_target_address
                if preferred_target_address in discovered_addresses
                else int(supplies[0]["address"])
            )

        with open(output_path, "w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=build_fieldnames(supplies))
            writer.writeheader()

            def activate_hv_dual(trigger_label):
                # Common re-entry path for phase-1 start and phase-2 retries.
                nonlocal hv_programmed_limits
                nonlocal hv_confirmation_text
                nonlocal hv_arm_consumed
                nonlocal hv_phase
                nonlocal hv_active_addresses
                nonlocal hv_phase1_stable_since
                nonlocal hv_single_address
                if _stop_requested(stop_event):
                    return False
                print("Applying HV max mode programming...")
                hv_programmed_limits = _run_with_io_lock(
                    io_lock,
                    lambda: program_hv_max_for_addresses(
                        logger,
                        hv_addresses,
                        limit_source=hv_limit_source_normalized,
                    ),
                )
                hv_confirmation_text = summarize_programmed_limits(hv_programmed_limits)
                print(f"HV_CONFIRM [{trigger_label}] {hv_confirmation_text}")
                _safe_status_emit(status_callback, f"HV confirmed: {hv_confirmation_text}")
                for address in sorted(hv_programmed_limits):
                    if _stop_requested(stop_event):
                        _safe_hv_shutdown(
                            logger=logger,
                            hv_addresses=hv_addresses,
                            io_lock=io_lock,
                        )
                        return False
                    _run_with_io_lock(
                        io_lock,
                        lambda address=address: logger.turn_on(address=address),
                    )
                    if address not in started_addresses:
                        started_addresses.append(address)
                if _stop_requested(stop_event):
                    _safe_hv_shutdown(
                        logger=logger,
                        hv_addresses=hv_addresses,
                        io_lock=io_lock,
                    )
                    return False
                _run_with_io_lock(io_lock, logger.global_on)
                hv_arm_consumed = True
                hv_phase = "phase1"
                hv_active_addresses = sorted(hv_programmed_limits)
                hv_phase1_stable_since = None
                hv_single_address = None
                hv_voltage_windows.clear()
                hv_trip_condition_since.clear()
                for address in hv_active_addresses:
                    hv_voltage_windows[int(address)] = []
                return True

            # GUI Start button can run logging-only scans; Global ON paths set start_outputs=True.
            if start_outputs:
                if hv_mode:
                    if not activate_hv_dual("Start"):
                        _safe_status_emit(status_callback, "HV activation cancelled.")
                elif mode_normalized == "auto":
                    if target_address is None:
                        target_address = int(supplies[0]["address"])
                    print(f"Applying stimulus on PS{target_address}...")
                    _run_with_io_lock(
                        io_lock,
                        lambda: logger.set_voltage(address=target_address, voltage=2.0),
                    )
                    _run_with_io_lock(
                        io_lock,
                        lambda: logger.turn_on(address=target_address),
                    )
                    started_addresses.append(target_address)
                    _run_with_io_lock(io_lock, logger.global_on)
                else:
                    print("Applying manual overrides...")
                    for supply in supplies:
                        addr = int(supply["address"])
                        settings = normalized_overrides.get(addr, {})

                        if "voltage" in settings:
                            _run_with_io_lock(
                                io_lock,
                                lambda addr=addr, settings=settings: logger.set_voltage(
                                    address=addr,
                                    voltage=settings["voltage"],
                                ),
                            )
                        if "current" in settings:
                            _run_with_io_lock(
                                io_lock,
                                lambda addr=addr, settings=settings: logger.set_current(
                                    address=addr,
                                    current=settings["current"],
                                ),
                            )
                        if settings:
                            _run_with_io_lock(
                                io_lock,
                                lambda addr=addr: logger.turn_on(address=addr),
                            )
                            started_addresses.append(addr)
                    _run_with_io_lock(io_lock, logger.global_on)
            else:
                print("Logging-only start: output states unchanged.")
                _safe_status_emit(status_callback, "Logging-only mode active.")

            diag_every_n = (
                int(hv_full_diag_every_n)
                if hv_mode
                else int(full_diag_every_n)
            )
            cycle_index = 0
            sequence = 0

            print("Starting continuous logging...")
            while not _stop_requested(stop_event):
                if control_request_event is not None and control_request_event.is_set():
                    _sleep_with_stop(0.002, stop_event)
                    if _stop_requested(stop_event):
                        break
                    continue

                full_diagnostics = (cycle_index % diag_every_n) == 0
                t_now = time.time()
                row = _run_with_io_lock(
                    io_lock,
                    lambda: logger.measure_all_supplies(
                        supplies=supplies,
                        timestamp=t_now,
                        full_diagnostics=full_diagnostics,
                        control_request_event=control_request_event,
                    ),
                )
                measure_complete_ts = time.time()
                _mark_first_post_control_measurement(
                    control_timing=control_timing,
                    control_timing_lock=control_timing_lock,
                    measurement_ts=measure_complete_ts,
                )

                if print_rows:
                    print_live_row(t_now, row, supplies)
                writer.writerow(row)

    
                # sequence = 0 # NOTE: sequence is a global variable that is used to track the sequence number of the snapshot. INITIALIZED TO 0. FOR LINE 1415 TO WORK.
                # COMMENTED OUT: resetting sequence inside loop pins it near zero forever.
                # Keep sequence lifecycle as: init once before loop, increment per publish.
                try:
                    publish_snapshot(tdk_snapshot_path, row, sequence)
                    sequence += 1
                except Exception as snapshot_exc:
                    # Snapshot failure should not stop core instrument logging.
                    print(f"Snapshot publish warning: {snapshot_exc}")

                
                handle.flush()
                if _stop_requested(stop_event):
                    break

                '''
                Snapshot publish hook belongs exactly at this row boundary.
                Example call-site pattern for consistency:
                publish_snapshot(tdk_snapshot_path, row, sequence_counter)
                sequence_counter += 1
                '''
                reason = None
                hv_eval_supplies = supplies
                runtime_supplies = supplies
                if hv_mode:
                    hv_eval_supplies = _supplies_for_addresses(supplies, hv_active_addresses)
                    runtime_supplies = hv_eval_supplies
                    if hv_eval_supplies:
                        reason = _evaluate_hv_trip(
                            row=row,
                            supplies=hv_eval_supplies,
                            timestamp=t_now,
                            voltage_windows=hv_voltage_windows,
                            condition_since=hv_trip_condition_since,
                        )

                if reason is None and runtime_supplies:
                    reason = _evaluate_runtime_fault(
                        row=row,
                        supplies=runtime_supplies,
                    )
                    if reason and not full_diagnostics:
                        detail_ts = time.time()
                        detailed_row = _run_with_io_lock(
                            io_lock,
                            lambda: logger.measure_all_supplies(
                                supplies=supplies,
                                timestamp=detail_ts,
                                full_diagnostics=True,
                                control_request_event=None,
                            ),
                        )
                        detailed_reason = _evaluate_runtime_fault(
                            row=detailed_row,
                            supplies=runtime_supplies,
                        )
                        if print_rows:
                            print_live_row(detail_ts, detailed_row, supplies)
                        writer.writerow(detailed_row)
                        handle.flush()
                        if detailed_reason:
                            reason = detailed_reason

                if hv_mode:
                    if reason:
                        hv_trip_count += 1
                        hv_last_trip_reason = reason
                        hv_last_trip_timestamp = t_now
                        trip_reason = reason
                        trip_timestamp = t_now
                        print(reason)
                        hv_trip_history.append(t_now)
                        cooldown = _compute_hv_retry_cooldown(hv_trip_history, t_now)
                        hv_cooldown_until = t_now + cooldown
                        hv_phase = "phase2"
                        hv_phase1_stable_since = None
                        hv_single_address = None
                        hv_active_addresses = []
                        hv_voltage_windows.clear()
                        hv_trip_condition_since.clear()
                        _safe_hv_shutdown(
                            logger=logger,
                            hv_addresses=hv_addresses,
                            io_lock=io_lock,
                        )
                        _safe_status_emit(
                            status_callback,
                            f"{reason} Auto retry in {cooldown:.1f}s.",
                        )
                    elif hv_phase == "phase2":
                        if t_now >= hv_cooldown_until:
                            try:
                                if activate_hv_dual("AutoRetry"):
                                    _safe_status_emit(
                                        status_callback,
                                        "HV auto-retry engaged (dual-PS on).",
                                    )
                            except Exception as exc:
                                retry_reason = f"HV restart failed: {exc}"
                                hv_trip_count += 1
                                hv_last_trip_reason = retry_reason
                                hv_last_trip_timestamp = t_now
                                trip_reason = retry_reason
                                trip_timestamp = t_now
                                print(retry_reason)
                                hv_trip_history.append(t_now)
                                cooldown = _compute_hv_retry_cooldown(hv_trip_history, t_now)
                                hv_cooldown_until = t_now + cooldown
                                hv_phase = "phase2"
                                hv_phase1_stable_since = None
                                hv_single_address = None
                                hv_active_addresses = []
                                hv_voltage_windows.clear()
                                hv_trip_condition_since.clear()
                                _safe_hv_shutdown(
                                    logger=logger,
                                    hv_addresses=hv_addresses,
                                    io_lock=io_lock,
                                )
                                _safe_status_emit(
                                    status_callback,
                                    f"{retry_reason} Next retry in {cooldown:.1f}s.",
                                )
                    elif hv_phase == "phase1":
                        if hv_phase1_stable_since is None:
                            hv_phase1_stable_since = t_now
                        # Hold dual-PS operation for a settle window before phase-3 selection.
                        if (t_now - hv_phase1_stable_since) >= HV_PHASE1_STABLE_SECONDS:
                            keep_address, off_address = _pick_phase3_supply(
                                row=row,
                                active_addresses=hv_active_addresses,
                            )
                            if keep_address is None:
                                phase3_reason = "HV phase3 selection failed: voltage unreadable."
                                hv_trip_count += 1
                                hv_last_trip_reason = phase3_reason
                                hv_last_trip_timestamp = t_now
                                trip_reason = phase3_reason
                                trip_timestamp = t_now
                                print(phase3_reason)
                                hv_trip_history.append(t_now)
                                cooldown = _compute_hv_retry_cooldown(hv_trip_history, t_now)
                                hv_cooldown_until = t_now + cooldown
                                hv_phase = "phase2"
                                hv_phase1_stable_since = None
                                hv_single_address = None
                                hv_active_addresses = []
                                hv_voltage_windows.clear()
                                hv_trip_condition_since.clear()
                                _safe_hv_shutdown(
                                    logger=logger,
                                    hv_addresses=hv_addresses,
                                    io_lock=io_lock,
                                )
                                _safe_status_emit(
                                    status_callback,
                                    f"{phase3_reason} Auto retry in {cooldown:.1f}s.",
                                )
                            else:
                                if off_address is not None:
                                    _run_with_io_lock(
                                        io_lock,
                                        lambda address=off_address: logger.turn_off(
                                            address=address
                                        ),
                                    )
                                hv_single_address = int(keep_address)
                                hv_active_addresses = [int(keep_address)]
                                hv_phase = "phase4"
                                hv_voltage_windows.clear()
                                hv_trip_condition_since.clear()
                                hv_voltage_windows[int(keep_address)] = []
                                phase3_msg = (
                                    f"HV phase3 complete ({HV_PHASE3_SHUTOFF_POLICY}): PS{keep_address} ON, PS{off_address} OFF."
                                    if off_address is not None
                                    else f"HV phase3 complete ({HV_PHASE3_SHUTOFF_POLICY}): keeping PS{keep_address} ON."
                                )
                                print(phase3_msg)
                                _safe_status_emit(status_callback, phase3_msg)
                elif reason:
                    tripped = True
                    trip_reason = reason
                    trip_timestamp = t_now
                    print(reason)
                    break

                cycle_index += 1
                _sleep_with_stop(sample_period_s, stop_event)

            if tripped and not hv_mode:
                _safe_hv_shutdown(
                    logger=logger,
                    hv_addresses=sorted(set(started_addresses)),
                    io_lock=io_lock,
                )

        return {
            "csv_path": output_path,
            "supplies": supplies,
            "target_address": target_address,
            "stopped": _stop_requested(stop_event),
            "tripped": tripped,
            "trip_reason": trip_reason,
            "trip_timestamp": trip_timestamp,
            "hv_mode": hv_mode,
            "hv_arm_consumed": hv_arm_consumed,
            "hv_programmed_limits": hv_programmed_limits,
            "hv_confirmation_text": hv_confirmation_text,
            "hv_phase": hv_phase,
            "hv_single_address": hv_single_address,
            "hv_trip_count": hv_trip_count,
            "hv_last_trip_reason": hv_last_trip_reason,
            "hv_last_trip_timestamp": hv_last_trip_timestamp,
        }
    finally:
        try:
            _run_with_io_lock(io_lock, logger.global_off)
        except Exception:
            pass

        if hv_mode:
            for addr in sorted(set(hv_addresses or started_addresses)):
                try:
                    _run_with_io_lock(
                        io_lock,
                        lambda addr=addr: logger.turn_off(address=addr),
                    )
                except Exception:
                    pass
        elif mode_normalized == "auto" and target_address is not None:
            try:
                _run_with_io_lock(
                    io_lock,
                    lambda: logger.turn_off(address=target_address),
                )
            except Exception:
                pass
        elif mode_normalized == "manual":
            # Manual mode can start multiple channels, so shut down all started channels.
            for addr in sorted(set(started_addresses)):
                try:
                    _run_with_io_lock(
                        io_lock,
                        lambda addr=addr: logger.turn_off(address=addr),
                    )
                except Exception:
                    pass

        if owns_session and close_session:
            logger.close()
            print("TDK session closed.")


if __name__ == "__main__":
    rm = pyvisa.ResourceManager()
    print("Resources:", rm.list_resources())
    rm.close()
    run_logging_session(csv_path=csv_filename)
