import tkinter as tk
from tkinter import ttk, filedialog
import threading
import ipaddress
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor


def ping_host(ip):
    try:
        result = subprocess.run(
            ["ping", "-n", "1", "-w", "1000", ip],
            capture_output=True, text=True
        )
        output = result.stdout.lower()

        if any(err in output for err in [
            "could not find host",
            "destination host unreachable",
            "request timed out",
            "general failure"
        ]):
            return False, None

        for line in result.stdout.splitlines():
            if "time=" in line.lower():
                try:
                    latency = int(line.split("time=")[1].split("ms")[0].strip())
                    return True, latency
                except Exception:
                    pass
        return True, None
    except Exception:
        return False, None


class PingMonitorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Ping Monitor")

        self.interval = 5
        self.executor = ThreadPoolExecutor(max_workers=50)
        self.lock = threading.Lock()

        self.ip_list, self.hostname_list, self.rack_list = [], [], []
        self.stats = {}

        # sort state
        self.sort_col = None
        self.sort_reverse = False

        self.setup_ui()

    def setup_ui(self):
        top_frame = ttk.Frame(self.root)
        top_frame.pack(fill=tk.X, padx=5, pady=5)

        # Browse Buttons
        ttk.Button(top_frame, text="Browse IPs", command=self.load_ips).pack(side=tk.LEFT, padx=2)
        ttk.Button(top_frame, text="Browse Hostnames", command=self.load_hostnames).pack(side=tk.LEFT, padx=2)
        ttk.Button(top_frame, text="Browse Racks", command=self.load_racks).pack(side=tk.LEFT, padx=2)

        # Refresh Interval
        ttk.Label(top_frame, text="Refresh:").pack(side=tk.LEFT, padx=5)
        self.interval_var = tk.StringVar(value="5")
        interval_combo = ttk.Combobox(
            top_frame,
            textvariable=self.interval_var,
            values=["2", "5", "10", "30", "60", "180", "300", "600"],
            width=5, state="readonly"
        )
        interval_combo.pack(side=tk.LEFT, padx=2)

        # Search
        self.search_var = tk.StringVar()
        ttk.Entry(top_frame, textvariable=self.search_var, width=20).pack(side=tk.LEFT, padx=5)
        ttk.Button(top_frame, text="Search", command=self.apply_search).pack(side=tk.LEFT, padx=2)

        # Quit
        ttk.Button(top_frame, text="Quit", command=self.root.quit).pack(side=tk.RIGHT, padx=2)

        # Table (Unmounted first)
        self.columns = ("Unmounted", "IP Address", "Hostname", "Rack",
                        "Sent", "Received", "Loss%", "Avg", "Last")
        self.tree = ttk.Treeview(self.root, columns=self.columns, show="headings", height=25)
        for col in self.columns:
            self.tree.heading(col, text=col, command=lambda c=col: self.sort_by_column(c, False))
            self.tree.column(col, width=100, anchor=tk.CENTER)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Define color tags
        self.tree.tag_configure("alive", background="#b4ffb4")     # light green
        self.tree.tag_configure("down", background="#f8d4d4")      # light red
        self.tree.tag_configure("partial", background="#fff8b4")   # light yellow
        self.tree.tag_configure("unmounted", background="#cce5ff") # light blue

        # Bind toggle for "Unmounted"
        self.tree.bind("<Double-1>", self.toggle_unmounted)

        # Status bar
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(self.root, textvariable=self.status_var, anchor="w").pack(fill=tk.X, side=tk.BOTTOM)

    def load_ips(self):
        file = filedialog.askopenfilename(filetypes=[("Text files", "*.txt")])
        if not file:
            return
        with open(file) as f:
            self.ip_list = [line.strip() for line in f if line.strip()]
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
        with open(file) as f:
            self.hostname_list = [line.strip() for line in f if line.strip()]
        self.refresh_table()

    def load_racks(self):
        file = filedialog.askopenfilename(filetypes=[("Text files", "*.txt")])
        if not file:
            return
        with open(file) as f:
            self.rack_list = [line.strip() for line in f if line.strip()]
        self.refresh_table()

    def _row_values_and_tag(self, ip, idx):
        hostname = self.hostname_list[idx] if idx < len(self.hostname_list) else ""
        rack = self.rack_list[idx] if idx < len(self.rack_list) else ""
        s = self.stats[ip]

        if s["unmounted"]:
            tag = "unmounted"
        elif s["fail_count"] >= 3:   # 🔴 mark red only after 3 fails in a row
            tag = "down"
        elif not s.get("last_ok", True):  # 🟡 mark yellow if last ping failed
            tag = "partial"
        else:
            tag = "alive"

        values = (
            "✔" if s["unmounted"] else " ",
            ip, hostname, rack, s["sent"], s["recv"], s["loss"], s["avg"], s["last"]
        )
        return values, tag

    def refresh_table(self, initial=False):
        existing_iids = set(self.tree.get_children(""))
        desired_iids = set(self.ip_list)
        for iid in existing_iids - desired_iids:
            self.tree.delete(iid)

        for idx, ip in enumerate(self.ip_list):
            values, tag = self._row_values_and_tag(ip, idx)
            if ip in existing_iids:
                self.tree.item(ip, values=values, tags=(tag,))
            else:
                self.tree.insert("", "end", iid=ip, values=values, tags=(tag,))

        if self.sort_col is not None:
            self._apply_sort(self.sort_col, self.sort_reverse)

        if initial:
            self.update_status_bar()

    def update_status_bar(self):
        alive = sum(1 for ip in self.ip_list if self.stats[ip]["alive"])
        down = len(self.ip_list) - alive
        self.status_var.set(
            f"Total: {len(self.ip_list)} | Alive: {alive} | Down: {down} | Last Update: {time.strftime('%H:%M:%S')}"
        )

    def run_monitor(self):
        try:
            self.interval = int(self.interval_var.get())
        except ValueError:
            self.interval = 5
        for ip in self.ip_list:
            self.executor.submit(self.ping_and_update, ip)
        self.root.after(self.interval * 1000, self.run_monitor)

    def ping_and_update(self, ip):
        ok, latency = ping_host(ip)
        with self.lock:
            s = self.stats[ip]
            s["sent"] += 1

            if ok and latency is not None:
                s["recv"] += 1
                s["last"] = latency
                s["avg"] = int(((s["avg"] * (s["recv"] - 1)) + latency) / max(1, s["recv"]))
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

    def _apply_sort(self, col, reverse):
        items = [(self.tree.set(k, col), k) for k in self.tree.get_children("")]

        def cast(val):
            try:
                return float(val)
            except Exception:
                return val

        if col == "IP Address":
            items.sort(key=lambda t: ipaddress.ip_address(t[0]), reverse=reverse) # type: ignore
        elif col in ("Sent", "Received", "Loss%", "Avg", "Last"):
            items.sort(key=lambda t: cast(t[0]), reverse=reverse) # type: ignore
        else:
            items.sort(key=lambda t: t[0], reverse=reverse) # type: ignore

        for idx, (_, k) in enumerate(items):
            self.tree.move(k, "", idx)

    def sort_by_column(self, col, reverse):
        self._apply_sort(col, reverse)
        self.sort_col = col
        self.sort_reverse = reverse
        for c in self.columns:
            if c == col:
                self.tree.heading(c, command=lambda cc=c: self.sort_by_column(cc, not reverse))
            else:
                self.tree.heading(c, command=lambda cc=c: self.sort_by_column(cc, False))

    def apply_search(self):
        query = self.search_var.get().lower().strip()
        for ip in self.ip_list:
            if ip not in self.stats:
                continue
            values = self.tree.item(ip, "values")
            if query == "" or any(query in str(v).lower() for v in values[1:4]):
                try:
                    self.tree.reattach(ip, "", "end")  # re-show
                except Exception:
                    pass
            else:
                try:
                    self.tree.detach(ip)  # hide
                except Exception:
                    pass

        # ✅ Always reapply sorting after search
        if self.sort_col is not None:
            self._apply_sort(self.sort_col, self.sort_reverse)

    def toggle_unmounted(self, event):
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return

        col = self.tree.identify_column(event.x)
        col_index = int(col.replace("#", "")) - 1
        if self.tree["columns"][col_index] != "Unmounted":
            return

        item = self.tree.identify_row(event.y)
        if not item:
            return

        ip = self.tree.item(item, "values")[1]
        self.stats[ip]["unmounted"] = not self.stats[ip]["unmounted"]
        idx = self.ip_list.index(ip)
        values, tag = self._row_values_and_tag(ip, idx)
        self.tree.item(ip, values=values, tags=(tag,))


if __name__ == "__main__":
    root = tk.Tk()
    app = PingMonitorApp(root)
    root.mainloop()
