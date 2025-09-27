import tkinter as tk
from ui import PingMonitorApp


def main():
    root = tk.Tk()
    app = PingMonitorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
