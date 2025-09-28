import tkinter as tk
from tkinter import ttk, filedialog
import threading
import time
import platform
import pyperclip
import sys, os
from concurrent.futures import ThreadPoolExecutor

from ping import ping_host
from fileops import read_list_from_file, export_to_csv


class PingMonitorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Ping Monitor")

        self.interval = 5
        self.executor = ThreadPoolExecutor(max_workers=50)
        self.lock = threading.Lock()

        self.ip_list, self.hostname_list, self.rack_list = [], [], []
        self.stats = {}

        self.sort_col = None
        self.sort_reverse = False

        self.monitor_thread = None
        self.monitor_running = False
        self.stop_event = threading.Event()

        self.clear_selection_job = None   # keep track of scheduled job

        self.unmounted = set()

        self.setup_ui()

    # ------------------ UI Setup ------------------
    def setup_ui(self):
        top_frame = ttk.Frame(self.root)
        top_frame.pack(fill=tk.X,side=tk.TOP, padx=5, pady=5)

        ttk.Button(top_frame, text="Browse IPs", command=self.load_ips).pack(side=tk.LEFT, padx=2)
        ttk.Button(top_frame, text="Browse Hostnames", command=self.load_hostnames).pack(side=tk.LEFT, padx=2)
        ttk.Button(top_frame, text="Browse Racks", command=self.load_racks).pack(side=tk.LEFT, padx=2)

        ttk.Label(top_frame, text="Refresh:").pack(side=tk.LEFT, padx=5)
        self.interval_var = tk.StringVar(value="5")
        interval_combo = ttk.Combobox(
            top_frame,
            textvariable=self.interval_var,
            values=["2", "5", "10", "30", "60", "180", "300", "600"],
            width=5, state="readonly"
        )
        interval_combo.pack(side=tk.LEFT, padx=2)

        self.search_var = tk.StringVar()
        ttk.Label(top_frame, text="Search:").pack(side=tk.LEFT, padx=5)
        ttk.Entry(top_frame, textvariable=self.search_var, width=20).pack(side=tk.LEFT, padx=5)
        ttk.Button(top_frame, text="Quit", command=self.quit_app).pack(side=tk.RIGHT, padx=2)
        ttk.Button(top_frame, text="Export CSV", command=self.export_csv).pack(side=tk.RIGHT, padx=2)
        ttk.Button(top_frame, text="Reset Unmounted", command=self.reset_unmounted).pack(side=tk.RIGHT, padx=2)


        # --- Treeview (middle, expands) ---
        self.columns = ("Unmounted", "IP Address", "Hostname", "Rack",
                        "Sent", "Received", "Loss%", "Avg", "Last")
        self.tree = ttk.Treeview(self.root, columns=self.columns, show="headings")
        for col in self.columns:
            self.tree.heading(col, text=col, command=lambda c=col: self.sort_by_column(c, False))
            self.tree.column(col, width=100, anchor=tk.CENTER)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Configure tags
        self.tree.tag_configure("alive", background="#b4ffb4")
        self.tree.tag_configure("down", background="#f8d4d4")
        self.tree.tag_configure("partial", background="#fff8b4")
        self.tree.tag_configure("unmounted", background="#cce5ff")
        self.tree.tag_configure("blueblink", background="#3399ff")

        # Bindings
        self.tree.bind("<Double-1>", self.toggle_unmounted)
        self.root.bind("<space>", self.toggle_unmounted_key)
        self.tree.bind("<Button-3>", self.copy_cell_to_clipboard)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        status_frame = ttk.Frame(self.root)
        status_frame.pack(fill=tk.X, side=tk.BOTTOM)
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(status_frame, textvariable=self.status_var, anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.message_var = tk.StringVar(value="")
        ttk.Label(status_frame, textvariable=self.message_var, anchor="e").pack(side=tk.RIGHT, padx=5)

    def reset_unmounted(self):
        """Clear unmounted flags for all IPs."""
        for ip in self.stats:
            if self.stats[ip].get("unmounted", False):
                self.stats[ip]["unmounted"] = False
        self.refresh_table()
        self.message_var.set("Unmounted ticks reset")


    # ------------------ Data Loaders ------------------
    def load_ips(self):
        self.stop_monitor()
        file = filedialog.askopenfilename(filetypes=[("Text files", "*.txt")])
        if not file:
            return
        self.ip_list = read_list_from_file(file)
        existing_unmounted = {ip: self.stats.get(ip, {}).get("unmounted", False) for ip in self.ip_list}
        self.stats = {
            ip: {
                "sent": 0, "recv": 0, "loss": 0, "avg": 0, "last": "-",
                "unmounted": existing_unmounted.get(ip, False),
                "fail_count": 0, "alive": False, "last_ok": True
            }
            for ip in self.ip_list
        }
        self.refresh_table(initial=True)
        self.run_monitor()

    def load_hostnames(self):
        file = filedialog.askopenfilename(filetypes=[("Text files", "*.txt")])
        if not file:
            return
        self.hostname_list = read_list_from_file(file)
        self.refresh_table()

    def load_racks(self):
        file = filedialog.askopenfilename(filetypes=[("Text files", "*.txt")])
        if not file:
            return
        self.rack_list = read_list_from_file(file)
        self.refresh_table()

    # ------------------ Table Updates ------------------
    def _row_values_and_tag(self, ip, idx):
        hostname = self.hostname_list[idx] if idx < len(self.hostname_list) else ""
        rack = self.rack_list[idx] if idx < len(self.rack_list) else ""
        s = self.stats[ip]

        if s["unmounted"]:
            tag = "unmounted"
        elif s["fail_count"] >= 3:
            tag = "down"
        elif not s.get("last_ok", True):
            tag = "partial"
        else:
            tag = "alive"

        values = (
            "✔" if s["unmounted"] else " ",
            ip, hostname, rack,
            s["sent"], s["recv"], s["loss"],
            round(s["avg"], 2), s["last"]
        )
        return values, tag

    def refresh_table(self, initial=False):

        filter_text = self.search_var.get().strip().lower()
        for idx, ip in enumerate(self.ip_list):
            values, tag = self._row_values_and_tag(ip, idx)

            tags = (tag,)

            # If item exists → update, else → insert
            if self.tree.exists(ip):
                self.tree.item(ip, values=values, tags=tags)
            else:
                self.tree.insert("", "end", iid=ip, values=values, tags=tags)

            # Hide rows that don’t match filter
            if filter_text and filter_text not in " ".join(map(str, values)).lower():
                self.tree.detach(ip)
            else:
                self.tree.reattach(ip, "", "end")

        # Reapply sort if a column is sorted
        if self.sort_col:
            self._apply_sort(self.sort_col, self.sort_reverse)


    def update_status_bar(self):
        alive = sum(1 for ip in self.ip_list if self.stats[ip]["alive"])
        down = len(self.ip_list) - alive
        unmounted = sum(1 for ip in self.ip_list if self.stats[ip]["unmounted"])
        self.status_var.set(
            f"Total: {len(self.ip_list)} | Alive: {alive} | Down: {down} | "
            f"Unmounted: {unmounted} | Last Update: {time.strftime('%H:%M:%S')}"
        )

    # ------------------ Monitor and Ping ------------------
    def stop_monitor(self):
        self.monitor_running = False
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.stop_event.set()
            self.monitor_thread.join(timeout=2)
        self.monitor_thread = None
        self.stop_event.clear()

    def run_monitor(self):
        if self.monitor_running:
            return
        self.monitor_running = True
        self.stop_event.clear()

        def monitor_loop():
            while self.monitor_running and not self.stop_event.is_set():
                try:
                    self.interval = int(self.interval_var.get())
                except ValueError:
                    self.interval = 5
                for ip in self.ip_list:
                    if not self.monitor_running or self.stop_event.is_set():
                        break
                    self.executor.submit(self.ping_and_update, ip)
                if self.stop_event.wait(self.interval):
                    break

        self.monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        self.monitor_thread.start()

    def ping_and_update(self, ip):
        ok, latency = ping_host(ip)
        with self.lock:
            s = self.stats[ip]
            s["sent"] += 1

            if ok and latency is not None:
                s["recv"] += 1
                s["last"] = latency
                s["avg"] = ((s["avg"] * (s["recv"] - 1)) + latency) / max(1, s["recv"])
                s["fail_count"] = 0
                s["alive"] = True
                s["last_ok"] = True
            else:
                s["fail_count"] += 1
                if s["fail_count"] >= 3:
                    s["alive"] = False
                s["last_ok"] = False

            s["loss"] = round(100 * (1 - (s["recv"] / max(1, s["sent"]))), 1)

        self.root.after(0, self.refresh_table)
        self.root.after(0, self.update_status_bar)

    # ------------------ Other UI Actions ------------------
    def toggle_unmounted(self, event):
        item = self.tree.selection()
        if not item:
            return
        ip = item[0]
        self.stats[ip]["unmounted"] = not self.stats[ip]["unmounted"]
        self.refresh_table()

    def toggle_unmounted_key(self, event):
        self.toggle_unmounted(event)

    def copy_cell_to_clipboard(self, event):
        item_id = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if item_id and col:
            col_num = int(col.replace("#", "")) - 1
            values = self.tree.item(item_id, "values")
            if 0 <= col_num < len(values):
                pyperclip.copy(str(values[col_num]))
                self.message_var.set(f"Copied: {values[col_num]}")

    def apply_search(self):
        query = self.search_var.get().lower()
        for ip in self.ip_list:
            values = self.tree.item(ip, "values")
            match = any(query in str(v).lower() for v in values)
            self.tree.detach(ip) if not match else self.tree.reattach(ip, "", "end")

    def sort_by_column(self, col, reverse):
        self.sort_col = col
        self.sort_reverse = reverse
        self._apply_sort(col, reverse)

    def _apply_sort(self, col, reverse):
        l = [(self.tree.set(k, col), k) for k in self.tree.get_children("")]
        try:
            l.sort(key=lambda t: float(t[0]) if t[0] not in ("", "-") else float("-inf"), reverse=reverse)
        except ValueError:
            l.sort(key=lambda t: t[0], reverse=reverse)
        for index, (val, k) in enumerate(l):
            self.tree.move(k, "", index)
        self.tree.heading(col, command=lambda: self.sort_by_column(col, not reverse))

    def on_select(self, event):
        self.blinking_rows = set(self.tree.selection())

        # Cancel any previous scheduled clear
        if self.clear_selection_job:
            self.root.after_cancel(self.clear_selection_job)

        # Schedule a new clear 10 seconds later
        self.clear_selection_job = self.root.after(10000, self.clear_selection)

    def clear_selection(self):
        for item in self.tree.selection():
            self.tree.selection_remove(item)
        self.blinking_rows.clear()
        self.clear_selection_job = None


    def export_csv(self):
        file = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV files", "*.csv")])
        if not file:
            return
        data = []
        for ip in self.ip_list:
            idx = self.ip_list.index(ip)
            values, _ = self._row_values_and_tag(ip, idx)
            data.append(values)
        export_to_csv(file, data, self.columns)
        self.message_var.set(f"Exported results to {file}")

    def quit_app(self):
        self.monitor_running = False
        self.stop_event.set()
        try:
            self.executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        try:
            self.root.quit()
            self.root.destroy()
        except Exception:
            pass
        try:
            sys.exit(0)
        except SystemExit:
            os._exit(0)
