'''
This is the snapshot/Whiteboard file that will be used first:- by TDKlogic.py to write what it is logging, and then, so this will be a temporary RAM until we rename it to a json file once TDKs stop logging and then, the DAQ data logger - DAQ2700.py will read the json file and write the data to the same csv in which it was logging in based on the time delay threashold.

Treat TDK as the writer to a shared whiteboard and DAQ as the fast reader.
'''
#@author: HarshDesai

import json
import os, time, datetime
import tempfile #Temporary scratch space avoids corrupting final outputs during processing.
#Key idea: write intermediate result safely, then move/rename if success.

import shutil #shutil
#For moving/copying files after temp processing.

 
'''
NOW, HERE, as THIS is a helper file, WE SHOULD NEVER  import anything from the main project. Helper = neutral utility module
TDKLogic calls helper
DAQ2700 calls helper
If helper imports both apps, you create tight coupling/circular chaos.
'''
# from tdklambda import TDKLogic
# from DataCollection import DAQ2700


SNAPSHOT_ID = 1.0

TDK_FIELDS = [
    "ps_1_voltage",
    "ps_1_current",
    "ps_1_output_state",
    "ps_2_voltage",
    "ps_2_current",
    "ps_2_output_state",
]

TDK_METADATA_FIELDS = [
    "tdk_timestamp",
    "tdk_age_s",
    "tdk_status",
    "voltage_sum",
]

MERGED_FIELDS = TDK_FIELDS + TDK_METADATA_FIELDS
#list

# Team contract (solid-gold convention):
# 1) `read_snapshot(...) -> dict` communicates the expected object shape.
# 2) At call sites (DAQ/TDK), always store that return value as `snapshot`.
#    This keeps reasoning consistent across helper + logic scripts.
SNAPSHOT_CALLSITE_NAME = "snapshot"


'''
EXAMPLE run, on how to create a json. No tempfile created though.
'''
# # Example mock data for demo/testing print
example_row = {
    "timestamp": time.time(),
    "ps_1_voltage": 12.3,
    "ps_1_current": 0.56,
    "ps_1_output_state": "ON",
    "ps_2_voltage": 5.0,
    "ps_2_current": 0.22,
    "ps_2_output_state": "OFF",
}

# def publish_snapshot(path, row, sequence=0):
#     '''Publish a snapshot to the shared whiteboard and print the payload dict.'''
#     # ignore passed `path`, always use internal logic for snapshot filename
#     current_snapshot_file = os.path.join(current_dir, 'tdk_snapshot.json')
#     tdk_timestamp = row.get("timestamp")
#     if not tdk_timestamp:
#         raise ValueError("timestamp from TDKLogic.py is required")
#     payload_dict = {
#         "snapshot_version": SNAPSHOT_VERSION,
#         "sample_timestamp": tdk_timestamp,
#         "sequence": sequence,
#         "row": row
#     }
#     print(payload_dict)  # This will print the payload_dict so you can see its structure
#     with open(current_snapshot_file, 'w') as new_file:
#         json.dump(payload_dict, new_file, indent=4)
#     return current_snapshot_file
    

# # Call with example data to see the output.
# publish_snapshot(None, example_row, sequence=1)

current_dir = os.path.dirname(os.path.abspath(__file__))


def publish_snapshot(path, row, sequence):
    '''Write the latest TDK sample to disk atomically.
    Atomic write strategy: write a temp file in the same directory,
    then os.replace() swaps it in. The reader never sees a partial file.
    '''
    payload = {
        "id": SNAPSHOT_ID,
        "timestamp": row.get("timestamp"),
        "sequence": int(sequence),
        "published_at": time.time(),
        "fields": {k: row.get(k) for k in TDK_FIELDS},
    }

    final_path = os.path.abspath(path)
    final_dir = os.path.dirname(final_path)
    os.makedirs(final_dir, exist_ok=True)
    # METHOD 1: USING mkstemp()
    # temp = tempfile.mkstemp(dir=final_dir, suffix=".tmp")
        # fd, temp = tempfile.mkstemp(dir=final_dir, suffix=".tmp")
                # temp file is created in the final_dir directory and the file name is returned in the temp variable and the file descriptor is returned in the fd variable
        # with os.fdopen(fd, 'w') as f:
        #     json.dump(payload, f, indent=4)
        # os.close(fd)
        # os.replace(temp, final_path)
        # return final_path

        # temp file is created in the final_dir directory and the file name is returned in the temp variable and the file descriptor is returned in the fd variable
    # METHOD 2: USING NamedTemporaryFile()
    # temp = tempfile.NamedTemporaryFile(dir=final_dir, delete=False)
    # with open(temp.name, 'w') as f:
    #     json.dump(payload, f, indent=4)
    # temp.close()
    # os.replace(temp.name, final_path)
    #
    # COMMENTED OUT ON PURPOSE:
    # On Windows, keeping a NamedTemporaryFile handle open while re-opening that
    # path can intermittently fail due to file locking. Keep this for learning
    # history, but switch active write path to mkstemp() below.

    fd, temp = tempfile.mkstemp(dir=final_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(payload, f, indent=4)
        os.replace(temp, final_path)
    except Exception:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise
    return final_path


def read_snapshot(path) -> dict:
    '''Returns the snapshot payload dict on success, or None on any failure.
    
    VERY IMPORTANT NOTE: USE 'snapshot' variable in DAQ2700.py to access the snapshot.

    Returned dict keys: 'id', 'timestamp', 'sequence', 'published_at', 'fields'
    'fields' contains: ps_1_voltage, ps_1_current, ps_1_output_state, etc.
    """'''
    try:
        with open(path, 'r') as f:
            payload = json.load(f)
            # print(payload)
            # Keep this line commented to avoid log spam every DAQ cycle.
            required_fields = ['id','timestamp', 'sequence', 'published_at', 'fields']
            if not all(field in payload.keys() for field in required_fields):
                return None
            return payload
    except FileNotFoundError:
        print(f"Snapshot file not found: {path}")
        return None
    except json.JSONDecodeError:
        print(f"Error parsing snapshot JSON: {path}")
        return None
    except OSError as e:
        print(f"Error reading snapshot: {path}")
        return None
    except Exception as e:
        print(f"Error reading snapshot: {e}")
        return None   
    # NOTE: Order of except clauses: specific first (FileNotFoundError, json.JSONDecodeError, OSError), broad Exception last

def compute_freshness(snapshot, daq_timestamp, max_age_s=1.2, missing_after_s=None) -> tuple: 
    '''
    Compute the freshness of the snapshot.
    tuple of age and status, hence, when called, USE tdk_age_s, tdk_status = compute_freshness(snapshot, timestamp, 0.5)
    NOTE:
    Also, this is the FIRST TIME "snapshot" is used in this file. "payload" is now CONVERTED to "snapshot"!!!!!! THIS IS A BIG DEAL. cause in DAQ2700.py, you are calling read_snapshot to get the snapshot, and then you are calling compute_freshness to get the freshness of the snapshot. So, if you don't use the same variable name, you will get confused.
    '''
    # snapshot = read_snapshot(path) 
    # #read_snapshot is called inside here, but DAQ already called read_snapshot to get the snapshot. Now you're reading the file twice per DAQ row. That's wasteful and inconsistent — between the two reads, TDK might have published a new snapshot. DAQ would have two different versions.

    # daq_timestamp = time.time()
            #NOTE: daq_timestamp is already passed in from DAQ2700.py
            # daq_timestamp was passed in by DAQ for a reason — it's the exact timestamp DAQ used when it took the reading. You're throwing it away and replacing it with time.time() which is slightly later. That makes age slightly wrong.

    if snapshot is None:
        return None, "missing"
    if snapshot['id'] != SNAPSHOT_ID:
        return None, "invalid"
    if snapshot['timestamp'] is None:
        return None, "missing"

    # age = daq_timestamp - snapshot['timestamp']
    # if age > max_age_s:    #NOTE: OLD LOGIC KEPT FOR LEARNING HISTORY 

    # New logic (using float conversion):
    try:
        age = float(daq_timestamp) - float(snapshot['timestamp'])
    except (TypeError, ValueError):
        return None, "missing"

    # # Old logic (kept for learning history):
    # age = daq_timestamp - snapshot['timestamp']
    # if age > max_age_s:
    #     return age, "stale"

    if age < 0:
        return age, "missing"

    if missing_after_s is None:
        # Mark very old snapshots as missing publisher signal, not just stale.
        missing_after_s = float(max_age_s) * 3.0

    if age > float(missing_after_s):
        return age, "missing"
    if age > float(max_age_s):
        return age, "stale"
    # elif age < 0:
    #     return age, "missing"
    return age, "fresh"

def extract_tdk_columns(snapshot, age, status):
    '''Build the TDK portion of a merged DAQ row.

    Always includes tdk_timestamp, tdk_age_s, tdk_status.
    TDK field values are included when fresh or stale (so the analyst
    can see what the PSU was doing). Blanked only when truly missing.'''
    temp_dict = {}
    age = age if status == "fresh" or status == "stale" else None
    if status == "missing":
        # for field in snapshot['fields'].keys(): 
        # NOTE: no need to do this, because MERGED_FIELDS is already a list of all the fields. When status is "missing", snapshot is None.

        # None has no ['fields']. This crashes with TypeError.

        # That's the whole point of the missing branch — there's no snapshot to read from.
        for field in MERGED_FIELDS:
            temp_dict[field] = "" # NOTE: NEED TO FILL THIS WITH EMPTY STRINGS (""). Why? Because this dict gets written directly into a CSV row. None in a CSV becomes the literal text None. Empty string becomes a clean blank cell. For a scientist reading the CSV, blank is cleaner.
        temp_dict["tdk_status"] = "missing"
        temp_dict["tdk_age_s"] = ""
        return temp_dict
    # else:
    #     temp_dict = {k: snapshot['fields'].get(k) for k in TDK_FIELDS}
    #     if not all(temp_dict.values()):
    #         raise ValueError("Missing required TDK columns")
    #     status = "ok" if age <= max_age_s else "stale"
    #     return temp_dict, status
    else:
        
        # for field in MERGED_FIELDS and snapshot['fields'].get(field) is not None:
            #NOTE: this is not correct. we need to use the snapshot fields to get the values. not the snapshot itself. snapshot is not even declared in this function.

        for field in TDK_FIELDS:
            temp_dict[field] = snapshot['fields'].get(field, "")
            
            '''
            Step 1: loop through the list of field names.
            Step 2: for each name, GET the value from snapshot (with "" as safe default).
            '''
        try:
            temp_dict['voltage_sum'] = float(temp_dict['ps_1_voltage']) + float(temp_dict['ps_2_voltage'])
        except (TypeError, ValueError):
            temp_dict['voltage_sum'] = ""
        temp_dict['tdk_timestamp'] = snapshot['timestamp']
        temp_dict['tdk_age_s'] = age
        temp_dict['tdk_status'] = status
        return temp_dict



# publish_snapshot(current_dir+'/tdk_snapshot.json', example_row, sequence=1)
#if you use this directly, then the test call will be run. but it also runs when someone else imports it! Which is not what we want.hence we use __name__ == "__main__" to skip the test call when someone else imports it.

'''
__name__ is another one of those automatic variables Python gives every script. It equals "__main__" only when you run the file directly yourself. When someone else imports it, __name__ equals the filename instead - so the block is skipped entirely.

Translation in plain English:

"Only run this test call if I'm the one running this file on purpose. Not when someone else is just borrowing my functions."
'''
if __name__ == "__main__":
    publish_snapshot(current_dir+'/tdk_snapshot.json', example_row, sequence=1)