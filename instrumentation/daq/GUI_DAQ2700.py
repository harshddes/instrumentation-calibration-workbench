import tkinter as tk
from tkinter import filedialog, messagebox
import threading
import os
import datetime
from instrumentation.daq.DAQ2700 import DAQ2700

import sys
print("sys.executable:", sys.executable)
print("Main Python PID:", os.getpid())

RANGE_SELECTOR_ENABLED = False
FIXED_RANGE_LABEL = "1000V"

class DAQGUI:
    def __init__(self, root):
        self.root = root
        root.title("Keithley DAQ 2700 GUI")
        
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.folder_var = tk.StringVar(value="demo_logs")
        self.csv_name_var = tk.StringVar()
        self.num_channels_var = tk.StringVar(value="2")
        self.GPIB_var = tk.StringVar(value="27")
        self.channel_vars = []
        self.channel_num_vars = []
        self.stop_flag = threading.Event()
        self.daq_thread = None
        self.channel_range_options = ["Auto", "100 mV", "1V", "10V", "100V", "1000V", ""]
        self.channel_range_vars = []


        self.use_timestamp = tk.BooleanVar()
        self.build_widgets()
        self.update_channel_entries()
        root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def build_widgets(self):
        tk.Label(self.root, text="Save Folder:").grid(row=0, column=0)
        tk.Entry(self.root, textvariable=self.folder_var, width=40).grid(row=0, column=1)
        tk.Button(self.root, text="Choose...", command=self.choose_folder).grid(row=0, column=2)

        tk.Label(self.root, text="CSV File Name:").grid(row=1, column=0)
        tk.Entry(self.root, textvariable=self.csv_name_var).grid(row=1, column=1)

        self.csv_entry = tk.Entry(self.root, textvariable=self.csv_name_var)
        self.csv_entry.grid(row=1, column=1)
        tk.Checkbutton(self.root, text="Use timestamped filename", 
            variable=self.use_timestamp).grid(row=1, column=2, sticky="w")
        self.use_timestamp.trace_add('write', self.toggle_csv_entry)

        tk.Label(self.root, text="GPIB Address:").grid(row=2, column=0)
        tk.Entry(self.root, textvariable=self.GPIB_var).grid(row=2, column=1)

        tk.Label(self.root, text="Number of Channels:").grid(row=3, column=0)
        tk.Entry(self.root, textvariable=self.num_channels_var).grid(row=3, column=1)
        self.num_channels_var.trace_add("write", self.update_channel_entries)

        self.channels_frame = tk.Frame(self.root)
        self.channels_frame.grid(row=4, column=0, columnspan=3)

        tk.Button(self.root, text="Start", command=self.start_scan).grid(row=5, column=0)
        tk.Button(self.root, text="Stop", command=self.stop_scan).grid(row=5, column=1)

        

    def toggle_csv_entry(self, *args):
        if self.use_timestamp.get():
            self.csv_entry.configure(state='disabled')
        else:
            self.csv_entry.configure(state='normal')

    def choose_folder(self):
        folder = filedialog.askdirectory(initialdir=self.script_dir)
        if folder:
            self.folder_var.set(folder)

    def update_channel_entries(self, *args):
        for widget in self.channels_frame.winfo_children():
            widget.destroy()
        num_str = self.num_channels_var.get()
        num = int(num_str) if num_str.isdigit() else 0
        self.channel_vars.clear()
        self.channel_num_vars.clear()
        self.channel_range_vars.clear()
        for i in range(num):
            ch_var = tk.StringVar()
            self.channel_vars.append(ch_var)
            tk.Label(self.channels_frame, text=f"Channel {i+1} Name:").grid(row=i, column=0)
            tk.Entry(self.channels_frame, textvariable=ch_var).grid(row=i, column=1)
            ch_num_var = tk.StringVar(value=str(101 + i))
            self.channel_num_vars.append(ch_num_var)
            tk.Label(self.channels_frame, text=f"Chan {i+1} #:").grid(row=i, column=2)
            tk.Entry(self.channels_frame, textvariable=ch_num_var).grid(row=i, column=3)
            ch_range_var = tk.StringVar(value=FIXED_RANGE_LABEL)
            self.channel_range_vars.append(ch_range_var)
            if RANGE_SELECTOR_ENABLED:
                tk.Label(self.channels_frame, text="Range:").grid(row=i, column=4)
                tk.OptionMenu(self.channels_frame, ch_range_var, *self.channel_range_options).grid(row=i, column=5)
            # if self.channel_range_options[6] == ch_range_var.get():
            #     ch_range_var.set("100V")
            # COMMENTED OUT: defaulting every new channel to 100V can hide
            # user intent; explicit Auto default is safer for mixed-channel scans.

    def start_scan(self):
        print("Start scan called")
        self.stop_flag.clear()
        if self.daq_thread is None or not self.daq_thread.is_alive():
            print("Starting new DAQ thread")
            if self.use_timestamp.get():
                now = datetime.datetime.now()
                filename = f"DAQ_{now.strftime('%m%d%Y_%H%M%S')}"
                self.csv_name_var.set(filename)
            elif not self.csv_name_var.get().strip():
                messagebox.showerror("Error", "CSV file name is required when timestamp mode is off.")
                return
            self.daq_thread = threading.Thread(target=self.run_scan)
            self.daq_thread.start()

    def stop_scan(self):
        self.stop_flag.set()

    def run_scan(self):
        print("Run scan thread started")
        folder = self.folder_var.get().strip()
        if not folder:
            raise ValueError("Save folder is required.")
        os.makedirs(folder, exist_ok=True)

        filename = self.csv_name_var.get().strip()
        if not filename:
            raise ValueError("CSV file name is required.")
        if not filename.lower().endswith(".csv"):
            filename = f"{filename}.csv"

        csv_title = os.path.join(folder, filename)
        print("DAQ CSV path:", csv_title)
        channels = {self.channel_vars[i].get(): self.channel_num_vars[i].get() 
                    for i in range(len(self.channel_vars))}
        channel_setups = []
        if RANGE_SELECTOR_ENABLED:
            # Keep setup as an ordered list so each GUI row maps to one explicit
            # command, even if channel numbers are duplicated during user edits.
            for i in range(len(self.channel_num_vars)):
                channel_number = self.channel_num_vars[i].get().strip()
                range_value = self.channel_range_vars[i].get().strip()
                if not range_value:
                    range_value = "Auto"
                channel_setups.append((channel_number, range_value))
        try:
            # DAQ2700(csv_title, channels, self.GPIB_var.get(), stop_event=self.stop_flag)
            # COMMENTED OUT: constructor no longer blocks; scan start is now explicit.
            daq = DAQ2700(csv_title, channels, self.GPIB_var.get(), stop_event=self.stop_flag)
            if RANGE_SELECTOR_ENABLED:
                print("Requested channel setups:", channel_setups)
                for channel_number, range_value in channel_setups:
                    daq.ch_range(channel_number, range_value)
            daq.start_scan()
            pass
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def on_closing(self):
        print("GUI closing, setting stop flag.")
        self.stop_flag.set()
        if self.daq_thread and self.daq_thread.is_alive():
            print("Waiting for DAQ thread...")
            self.daq_thread.join()
            print("DAQ thread exited.")
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = DAQGUI(root)
    root.mainloop()
    print("GUI exited.")