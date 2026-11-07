"""Live check of self-healing yt-dlp: Parlor's real image with a yt-dlp from 2025 pinned (one YouTube no
longer serves), Docker's restart policy as in the compose file. Queue a YouTube download; it must wait
instead of failing, Parlor must update yt-dlp, restart to load it, and the same download must then finish
on its own. The container is removed at the end (--keep leaves it). Needs Docker and internet:
python tests/live_heal.py [--keep]"""
import socket
import subprocess
import sys
import time
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[1]
OLD = "2025.9.26"  # said "The page needs to be reloaded" to every YouTube video in November 2026
VIDEO = "jNQXAC9IVRw"  # "Me at the zoo": 19 seconds
NAME, PASSWORD = "healadmin", "Live-heal-pass-42!"  # pragma: allowlist secret (a made-up password for a throwaway container)
IMAGE, BOX = "parlor:heal-test", f"parlor-heal-{int(time.time())}"
with socket.socket() as _s:
    _s.bind(("127.0.0.1", 0))
    PORT = _s.getsockname()[1]
BASE, H = f"http://127.0.0.1:{PORT}", {"X-Requested-With": "parlor"}
# the image's own start-up update is skipped (it would replace the old yt-dlp before the test starts);
# the old one is pinned on the first start only, so the restart after healing keeps the new one
START = (f"[ -f /tmp/pinned ] || {{ pip install -q --root-user-action=ignore --disable-pip-version-check 'yt-dlp=={OLD}' "
         "&& touch /tmp/pinned; }; mkdir -p /music && cd /music && exec uvicorn --app-dir /app app:app --host 0.0.0.0 --port 8800")


def sh(*cmd, check=True):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode:
        sys.exit(f"{' '.join(cmd[:5])}… failed:\n{r.stdout[-500:]}\n{r.stderr[-1500:]}")
    return r.stdout.strip()


def inside(*cmd):
    return sh("docker", "exec", BOX, *cmd, check=False)


def wait(what, test, limit):
    end = time.time() + limit
    while time.time() < end:
        try:
            got = test()
            if got:
                return got
        except requests.RequestException:
            pass  # restarting
        time.sleep(3)
    print(sh("docker", "logs", "--tail", "40", BOX, check=False))
    sys.exit(f"FAIL: {what} within {limit}s")


def job(s):
    return next((j for j in s.get(f"{BASE}/api/yt/downloads", timeout=10).json() if j["id"] == VIDEO), None)


sh("nice", "-n", "19", "docker", "build", "-q", "-t", IMAGE, str(REPO))
sh("docker", "run", "-d", "--name", BOX, "--restart", "unless-stopped", "-p", f"127.0.0.1:{PORT}:8800",
   "-e", "UPDATE_HOURS=24", IMAGE, "sh", "-c", START)
try:
    s = requests.Session()
    wait("Parlor starts", lambda: requests.get(f"{BASE}/api/health", timeout=5).ok, 300)
    old = inside("yt-dlp", "--version")
    print("yt-dlp at start:", old)
    assert s.post(f"{BASE}/api/auth/setup", json={"name": NAME, "password": PASSWORD}, headers=H, timeout=30).ok
    r = s.post(f"{BASE}/api/yt/downloads", json={"id": VIDEO, "quality": "360"}, headers=H, timeout=30)
    assert r.ok, r.text
    first = wait("the download waits for a fix (not fails)",
                 lambda: (j := job(s)) and j["status"] in ("waiting", "failed", "done") and j, 300)
    print(f"  first try: {first['status']}: {first.get('error')}")
    assert first["status"] == "waiting" and "yt-dlp" in (first.get("error") or ""), first
    restarts = wait("Parlor restarts to load the new yt-dlp",
                    lambda: int(sh("docker", "inspect", "-f", "{{.RestartCount}}", BOX) or 0), 900)
    new = wait("Parlor is back", lambda: requests.get(f"{BASE}/api/health", timeout=5).ok and inside("yt-dlp", "--version"), 300)
    print(f"  restarted {restarts}x; yt-dlp now {new}")
    assert new and new != old, new
    done = wait("the same download finishes on its own", lambda: (j := job(s)) and j["status"] in ("done", "failed") and j, 600)
    print(f"  then: {done['status']}: {done.get('error') or done.get('path')}")
    assert done["status"] == "done" and done["job"] == first["job"], done
    print("RESULT: ok (waited, healed, restarted, finished)")
finally:
    if "--keep" in sys.argv:
        print(f"left running: {BOX} at {BASE}")
    else:
        sh("docker", "rm", "-f", BOX, check=False)
