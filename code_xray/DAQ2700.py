from pymeasure.instruments.keithley import Keithley2700
from pymeasure.adapters import VISAAdapter
import csv
import time
import pyvisa
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tdk_snapshot import read_snapshot, compute_freshness, extract_tdk_columns, MERGED_FIELDS
'''
Shared helper usage contract:
- Always bind read_snapshot(...) output to variable name `snapshot`.
- DAQ owns `timestamp`; pass it into compute_freshness(...) unchanged.
- Keep the read-once pattern per DAQ row.
Example wiring at DAQ row boundary:
snapshot = read_snapshot(tdk_snapshot_path)
tdk_age_s, tdk_status = compute_freshness(snapshot, timestamp, tdk_max_age_s)
'''

TDK_MAX_AGE_S = 1.2
TDK_MISSING_AGE_S = 3.6

def DAQ2700 (csv_title,channels={},GPIB = "27", stop_event=None):
    print("DAQ2700 function PID:", os.getpid())
    print("DAQ2700 module file:", __file__)
    '''
    DAQ6009 operates a keithley DAQ6009 digitial multimeter. 
    filename points to the folder and name of the csv output. 
    channels is a dictionary with keywords beging {channel_name:channel}
    device_name is the name of the device
    sample rate is in hertz.
    samples_per_block is the Number to read/write in each block (1 second of data)
    '''
    # Cleaning channels dictionary 
    print("Started DAQ2700")
    channels = { k.strip():v.strip() for k, v in channels.items()}
    if not channels:
        raise ValueError("No channels provided for DAQ2700 scan.")
    #ch1 = 101
    #ch2 = 102

    # --- 1. Connect to the Keithley 2700 ---
    adapter = VISAAdapter('GPIB::'+GPIB)
    adapter.connection.timeout = 5e3  # Set to 5 second (1000 ms)
    k2700 = Keithley2700(adapter)


    # # ===== OLD SETUP BLOCK (kept for reference) =============================
    #     # --- 2. Configure the instrument ---
    #     k2700.write('SYST:PRES')              # Preset the system
    #     # k2700.write('SYST:ERR:CLEAR')         # Clear error queue
    #     # k2700.write('SYST:ERR:NEXT?')         # Check for errors
    #     # error = k2700.ask('SYST:ERR:NEXT?').strip()
    #     # if error != '0':
    #     #     raise RuntimeError(f"Keithley rejected preset: {error}")
    #     # print(f"Keithley preset accepted: {error}")
        
    # #previous code:
    #     # k2700.write('CONF:VOLT:DC:RANG 100') # Set the range to 100V
    #     # scan_text = 'ROUT:SCAN (@)'+','.join(channels.values())+')' # Scan only the channels specified


    # #new code:
    #     k2700.write('*CLS')                   # Clear status/error queue before setup

    #     scan_channels = ','.join(channels.values())
    #     scan_text =  f'ROUT:SCAN (@{scan_channels})'

    #     # Configure range on scan channels (not only global DMM state).
    #     k2700.write(f":FUNC 'VOLT',(@{scan_channels})")
    #     k2700.write(f':VOLT:DC:RANG:AUTO OFF,(@{scan_channels})')
    #     k2700.write(f':VOLT:DC:RANG 100,(@{scan_channels})')

    #     config_error = k2700.ask('SYST:ERR?').strip()
    #     if not config_error.startswith('0'):
    #         raise RuntimeError(f"Keithley rejected range/channel setup: {config_error}")

    #     k2700.write(scan_text)      # Scan only channels 101 and 102
    #     k2700.write('TRIG:SOUR IMM')          # Immediate trigger
    #     k2700.write('TRIG:COUN 1')            # Number of scans per trigger
    #     k2700.write(f'SAMP:COUN {len(channels.keys())}')            # Number of channels per scan
    #     k2700.write('FORM:ELEM READ')         # Only output the reading value (voltage)
    #     k2700.write('ROUT:SCAN:TSO IMM')      # Route Scan Trigger Source SET TO Immediate
    #     k2700.write('ROUT:SCAN:LSEL INT')     # Route Scan List Select SET TO Internal
    # # ===== END OLD SETUP BLOCK =============================================

    #new code:
    # ===== ACTIVE SETUP BLOCK (one-shot, multi-channel safe) =====
    k2700.write('SYST:PRES')
    k2700.write('*CLS')
    k2700.write('TRAC:CLE')
    k2700.write('INIT:CONT OFF')
    k2700.write('ABOR')

    scan_channels = ','.join(channels.values())
    scan_text = f'ROUT:SCAN (@{scan_channels})'

    # Apply function and range to the scan channels themselves
    k2700.write(f"FUNC 'VOLT',(@{scan_channels})")
    k2700.write(f'VOLT:DC:RANG:AUTO OFF,(@{scan_channels})')
    k2700.write(f'VOLT:DC:RANG 1000,(@{scan_channels})')

    k2700.write(scan_text)
    k2700.write('TRIG:SOUR IMM')
    k2700.write('TRIG:COUN 1')
    k2700.write(f'SAMP:COUN {len(channels)}')
    k2700.write('FORM:ELEM READ')
    k2700.write('ROUT:SCAN:TSO IMM')
    k2700.write('ROUT:SCAN:LSEL INT')

    # Drain setup errors so they don't accumulate into -350
    setup_errors = []
    while True:
        err = k2700.ask('SYST:ERR?').strip()
        if err.startswith('0'):
            break
        setup_errors.append(err)

    if setup_errors:
        raise RuntimeError("Keithley setup errors: " + " | ".join(setup_errors))

    print("Configured scan list:", k2700.ask('ROUT:SCAN?').strip())
    print("Configured SAMP:COUN:", k2700.ask('SAMP:COUN?').strip())
    print("Configured TRIG:COUN:", k2700.ask('TRIG:COUN?').strip())



    # --- 3. Set up CSV output ---
    with open(csv_title, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        # writer.writerow(['Timestamp']+ list(channels.keys()))
        # COMMENTED OUT: old header omitted merged TDK columns.
        daq_header = ['Timestamp'] + list(channels.keys())
        merged_header = daq_header + MERGED_FIELDS
        writer.writerow(merged_header)
        tdk_snapshot_path = os.path.join(PROJECT_ROOT, "tdk_snapshot.json")
        print("TDK snapshot path:", tdk_snapshot_path)

        print("Starting continuous scan...")
        try:
            while True:
                if stop_event is not None and stop_event.is_set():
                    print("Stop signal received. Ending scan.")
                    break
                # --- 4. Trigger scan and read voltages ---
                try:
                    data = k2700.ask('READ?').strip() # This triggers scan and returns readings
                except pyvisa.errors.VisaIOError as timeout_exc:
                    print(f"Timeout during READ?: {timeout_exc}")
                    continue
               
                # Split result into readings
                readings = data.split(',')
                
                timestamp = time.time()  # Or use time.strftime('%Y-%m-%d %H:%M:%S') for human-readable
                expected_count = len(channels.keys())
                normalized_readings = readings[:expected_count]
                
                if len(normalized_readings) < expected_count:
                    normalized_readings.extend([""] * (expected_count - len(normalized_readings)))
                '''
                Read the snapshot from the file.
                Compute the freshness of the snapshot.
                '''
                snapshot = read_snapshot(tdk_snapshot_path)
                # tdk_age_s, tdk_status = compute_freshness(snapshot, timestamp, 0.5)
                # COMMENTED OUT: 0.5s was too strict for observed TDK cadence (~0.6-1.0s).

                # tdk_age_s, tdk_status = compute_freshness
                # (snapshot, timestamp, 0.5) #NOTE: OLD LOGIC KEPT FOR LEARNING HISTORY 

                # New logic (using float conversion):
                tdk_age_s, tdk_status = compute_freshness(
                    snapshot,
                    timestamp,
                    TDK_MAX_AGE_S,
                    TDK_MISSING_AGE_S,
                )
                extracted_data = extract_tdk_columns(snapshot, tdk_age_s, tdk_status)
                merged_tdk_values = []
                for field in MERGED_FIELDS:
                    value = extracted_data.get(field, "")
                    merged_tdk_values.append("" if value is None else value)

                '''
                Write the data to the CSV file.
                '''
                writer.writerow([timestamp] + normalized_readings + merged_tdk_values)

                if len(readings) == len(channels.keys()):
                    # this is the print statemnent that reads the values in the terminal
                    tdk_status_live = str(extracted_data.get("tdk_status", "missing"))
                    tdk_age_live = extracted_data.get("tdk_age_s", "")
                    if tdk_age_live in ("", None):
                        tdk_age_text = "NA"
                    else:
                        try:
                            tdk_age_text = f"{float(tdk_age_live):.3f}s"
                        except (TypeError, ValueError):
                            tdk_age_text = str(tdk_age_live)
                    print(f"{timestamp}:"+ 
                    ', '.join(f'{key}:{readings[index]}' for index, key in enumerate(channels.keys())) +
                    f" | TDK status:{tdk_status_live} age:{tdk_age_text}"
                    )
                    # writer.writerow([timestamp]+ readings)
                    # COMMENTED OUT: duplicate row write after merged row.
                else:
                    print(
                        f"{timestamp}: Expected {len(channels)} readings, "
                        f"got {len(readings)} -> {readings} "
                        f"| TDK status:{extracted_data.get('tdk_status', 'missing')}"
                    )
                    runtime_err = k2700.ask('SYST:ERR?').strip()
                    if not runtime_err.startswith('0'):
                        print("Keithley runtime error:", runtime_err)
                    # writer.writerow([timestamp] + readings)
                    # COMMENTED OUT: merged row is already written above.

                #time.sleep(0.1)  # Adjust scan interval as desired

        except KeyboardInterrupt:
            print("Scan stopped by user.")
            k2700.write('ROUT:SCAN:LSEL NONE')
        finally:
            print("DAQ2700 exiting.")
            try:
                k2700.write('ABOR')        # Abort any ongoing operation
                k2700.write('TRAC:CLE')    # Clear instrument buffer
                k2700.write('INIT:CONT OFF')
                k2700.write('ROUT:SCAN:LSEL NONE')
            except Exception as e:
                print("Error during instrument shutdown:", e)
            k2700.shutdown()
            print("Keithley disconnected.")