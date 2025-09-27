import subprocess
import platform
import re


def ping_host(ip: str):
    """
    Cross-platform ping function.
    Returns: (alive: bool, latency: float|None)
    """
    try:
        system = platform.system().lower()
        if system == "windows":
            cmd = ["ping", "-n", "1", "-w", "1000", ip]
        else:
            cmd = ["ping", "-c", "1", "-W", "1", ip]

        result = subprocess.run(cmd, capture_output=True, text=True)
        output = result.stdout.lower()

        if any(err in output for err in [
            "could not find host",
            "destination host unreachable",
            "request timed out",
            "general failure",
            "100% packet loss"
        ]):
            return False, None

        for line in result.stdout.splitlines():
            match = re.search(r"time[=<]\s*([\d\.]+)", line.lower())
            if match:
                return True, float(match.group(1))
        return True, None
    except Exception:
        return False, None
