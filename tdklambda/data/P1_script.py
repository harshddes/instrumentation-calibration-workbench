import os
import pandas as pd

# Anchor all paths to where THIS script lives -- no ambiguity about working directory
BASE = os.path.dirname(os.path.abspath(__file__))

# TDK csv is in the same folder as this script
df1_path = os.path.join(BASE, 'TDK_04032026_162849.csv')

# DAQ csv: use the public synthetic merged-session example.
df2_path = os.path.join(BASE, '..', '..', 'examples', 'synthetic_merged_session.csv')


'''PART1: How to read a csv file into a dataframe
'''
df1 = pd.read_csv(df1_path)
df2 = pd.read_csv(df2_path)

# --- Inspect df1 (TDK) ---
print("=== TDK ===")
print("shape :", df1.shape)        # attribute, no ()
print("columns:\n", df1.columns)
print("dtypes:\n", df1.dtypes)     # attribute, no ()
print(df1.head(n=10))

# --- Inspect df2 (DAQ) ---
print("\n=== DAQ ===")
print("shape :", df2.shape)
print("columns:\n", df2.columns)
print("dtypes:\n", df2.dtypes)
print(df2.head())

'''PART2: How to select multiple columns from a dataframe
'''
# df1["timestamp", "ps_1_voltage", "ps_2_voltage", "ps_1_current", "ps_2_current", "ps_1_output_state", "ps_2_output_state"]
    # #This tries to select multiple columns from df1, but the syntax is wrong: df1["col1", "col2"] is not valid in pandas. You must use df1[["col1", "col2", ...]] (a list of strings, not a tuple) to select multiple columns. The current code will raise a KeyError.

    #WORKING:
# print("timestamp column:")
# y=df1["timestamp"]
# print(y.head())

# print("selected columns:")
# x=df1[["timestamp", "ps_1_voltage", "ps_2_voltage", "ps_1_current", "ps_2_current", "ps_1_output_state", "ps_2_output_state"]]
# print(x.head())


df1_cols = df1[["timestamp", "ps_1_voltage", "ps_2_voltage", "ps_1_current", "ps_2_current", "ps_1_output_state", "ps_2_output_state"]]
df2_cols = df2[["Timestamp", "V", "I"]]
# df1_selected=df1[df1_cols] 
# df2_selected=df2[df2_cols]
# now, this won't work because  you're passing a DataFrame as the index into another DataFrame. Pandas looks at that and thinks you're trying to do boolean masking (a filtering operation where you pass True/False values to select rows). It sees floats instead of booleans and crashes with exactly the error you got:
       #ValueError: Boolean array expected for the condition, not float64

df1_cols.head()
df2_cols.head()
print("shape: df1_cols.shape", df1_cols.shape)
print("shape: df2_cols.shape", df2_cols.shape)


'''PART3: small CLEANUP:
'''
df1_cols = df1_cols.rename(columns={"timestamp": "ts"})
df2_cols = df2_cols.rename(columns={"Timestamp": "ts"})

df1_cols = df1_cols.sort_values("ts").reset_index(drop=True)
df2_cols = df2_cols.sort_values("ts").reset_index(drop=True)

'''PART4: WHAT CAN WE DO WITH THEM?
'''
# STRATEGY 1: Dumb paste -- no time alignment whatsoever
# Row 0 of TDK gets pasted next to Row 0 of DAQ regardless of timestamps
# This ONLY makes sense if both were logged at exactly the same rate
# and started at exactly the same moment. They didn't. So this is physically wrong here.

# The shorter DataFrame determines how many rows survive.
# DAQ has 337 rows, TDK has 211 -- so 126 TDK rows have no DAQ counterpart -> NaN.

merged_paste = pd.concat([df1_cols.reset_index(drop=True), 
                           df2_cols.reset_index(drop=True)], axis=1) #axis=0 means concatenate rows, and axis=1 means concatenate columns

print("Strategy 1 shape:", merged_paste.shape)
print(merged_paste.head(10))
print("NaN count:\n", merged_paste.isna().sum())


import subprocess
import datetime
fname_temp = os.path.join(BASE, 'temp_view.csv')

# fname = f"temp_view_{datetime.datetime.now().strftime('%H%M%S')}.csv" if you want to add a timestamp to the file name

merged_paste.to_csv(fname_temp, index=False) #VVV IMP: Setting index=False tells to_csv not to write the DataFrame's index column to the CSV file. This keeps the output CSV cleaner, containing only your selected data columns.

# subprocess.Popen(["explorer", fname_temp]) 


# try:
#     import sys
#     if "temp_view.csv" in sys.modules:
#         sys.modules["temp_view.csv"].close()
# except Exception:
#     pass # WRONG: This will fail because the file is already open in the subprocess. Excel has the file locked at the operating system level. Python has zero control over that. sys.modules is a dictionary of Python's own imported code modules — things like pandas, os, etc. temp_view.csv is not a Python module, it will never be in sys.modules. That code does nothing relevant.
# The lock is held by Excel, and only Excel releasing it (i.e., you closing the file in Excel) fixes it.


# STRATEGY 2: Exact timestamp match
# "Only keep rows where TDK timestamp == DAQ timestamp exactly"
# For floating-point unix timestamps from two different instruments, this is basically never true.
# Result: nearly empty DataFrame.

merged_exact = pd.merge(df1_cols, df2_cols, on="ts", how="left")
# "inner" = only rows that exist in BOTH -- the intersection
# "left"  = keep all TDK rows, fill DAQ columns with NaN where no match
# "outer" = keep everything from both, NaN where no match on either side

print("Strategy 2 shape:", merged_exact.shape)
print(merged_exact.head(10))
# Expect: very few rows, maybe zero.
# This proves why exact float timestamp matching is useless for instrument data.




# STRATEGY 3: Nearest-timestamp merge -- the right tool for lab instrument data
# "For each TDK row, find the DAQ row whose timestamp is closest, within tolerance"
# If no DAQ row is within tolerance, that TDK row gets NaN for DAQ columns.

# REQUIREMENT: both DataFrames MUST be sorted ascending by the merge key
# (already done above)

# tolerance is in the same units as your timestamp -- unix seconds here
# TDK logs ~every 0.6-1s, DAQ slightly faster
# tolerance=1.0 means: "a DAQ reading within 1 second of a TDK reading is a valid match"

merged_asof = pd.merge_asof(
    df1_cols,           # left: every row of this gets a match attempt
    df2_cols,           # right: search for nearest row in here
    on="ts",            # the column to match on (must be sorted, same name in both)
    tolerance=1.0,      # max allowed time gap in seconds -- tune this to your sample rate
    direction="nearest" # look both forward and backward in time, not just backward
)
merged_asof["voltage_sum"] = merged_asof[["ps_1_voltage", "ps_2_voltage"]].sum(axis=1)

print("Strategy 3 shape:", merged_asof.shape)
print(merged_asof.head(10))
print("NaN count per column:\n", merged_asof.isnull().sum())
# NaN count tells you how many TDK rows had no DAQ reading within 1 second
# If NaN count is high, widen tolerance. If 0, you might be too loose.



out_path = os.path.join(BASE, 'merged_04032026_2.csv')
merged_asof.to_csv(out_path, index=False)
verify = pd.read_csv(out_path)
print("Output shape:", verify.shape)   # should match merged_asof.shape
print(verify.head())
subprocess.Popen(["explorer", out_path]) 