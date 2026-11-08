"""Idle memory of a real Parlor process: start it (throwaway folders, nothing signed in), let it settle, sample RSS."""
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

app = Path(sys.argv[1]).absolute()
py = Path(sys.argv[2]).absolute()
tmp = Path(tempfile.mkdtemp(prefix="parlor-rss-"))
for n in ("data", "videos", "music"):
    (tmp / n).mkdir()
s = socket.socket()
s.bind(("127.0.0.1", 0))
port = s.getsockname()[1]
s.close()
env = dict(os.environ, DATA_DIR=str(tmp / "data"), VIDEO_DIR=str(tmp / "videos"), MUSIC_DIR=str(tmp / "music"),
           SEERR_URL="", SEERR_API_KEY="", JELLYFIN_URL="", OPEN_SIGNUP="0", UPDATE_HOURS="0")
proc = subprocess.Popen(["nice", "-n", "19", str(py), "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
                        cwd=app, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(120):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2).read()
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    time.sleep(30)  # idle: nobody connected, background workers started

    def rss():
        total = 0
        for pid in [proc.pid] + [int(x) for x in subprocess.run(["pgrep", "-P", str(proc.pid)], capture_output=True, text=True).stdout.split()]:
            for line in Path(f"/proc/{pid}/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    total += int(line.split()[1])
        return total / 1024
    samples = []
    for _ in range(5):
        samples.append(rss())
        time.sleep(3)
    print("RSS MiB samples:", [round(x, 1) for x in samples], "median", round(sorted(samples)[2], 1))
finally:
    proc.terminate()
    proc.wait(timeout=20)
