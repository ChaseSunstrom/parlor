"""End-to-end check of yeerr's own compose: a brand-new folder, the real docker-compose.yml (Gluetun with
yeerr's front door switched to "VPN off", since there's no VPN here; no published ports), yeerr built
from this repo. Create the admin account over HTTP, wait for the automatic setup, check every app, then
run setup again: it must change nothing. Exit 0 when all of that holds. The stack is removed at the end
(--keep leaves it running for a look). Needs Docker and internet: python tests/live_stack.py [--keep]"""
import json
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[1]
RUN = Path(tempfile.mkdtemp(prefix="yeerr-")) / f"e2e-{int(time.time())}"  # fresh every time
NAME, PASSWORD = "stackadmin", "Live-setup-pass-42!"  # pragma: allowlist secret (a made-up password for a throwaway stack)
with socket.socket() as _s:  # a free port, so a stack left running from an earlier run doesn't get in the way
    _s.bind(("127.0.0.1", 0))
    PORT = _s.getsockname()[1]
BASE, H = f"http://127.0.0.1:{PORT}", {"X-Requested-With": "yeerr"}
OVERRIDE = """
services:
  yeerr:
    build: !reset null
    ports: !override ["127.0.0.1:{PORT}:8800"]
    environment:
      PUBLIC_INDEXERS: yts,eztv,thepiratebay,nyaasi,knaben
      UPDATE_HOURS: "0"
  setup:
    build: !reset null
  gluetun:            # the real one; its front door is told "VPN off" below, as there's no VPN file here
    ports: !reset []
""".replace("{PORT}", str(PORT)) + "".join(f"  {s}:\n    ports: !reset []\n" for s in ("prowlarr", "flaresolverr", "sonarr", "radarr", "lidarr", "bazarr", "jellyfin", "seerr", "cleanuparr"))


def sh(*cmd, check=True):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode:
        sys.exit(f"{' '.join(cmd[:5])}… failed:\n{r.stdout[-500:]}\n{r.stderr[-1500:]}")
    return r


def dc(*args, check=True):
    return sh("docker", "compose", "--project-directory", str(RUN), "-p", RUN.name, "-f", str(REPO / "docker-compose.yml"),
              "-f", str(RUN / "override.yml"), "--env-file", str(RUN / ".env"), *args, check=check)


def wait_for_run(s, since, need_password_run, limit=1500):
    """Poll Settings → Services until a run that started after `since` has finished."""
    end = time.time() + limit
    while time.time() < end:
        st = s.get(f"{BASE}/api/stack", timeout=30).json()
        run = st["run"]
        done = not run["running"] and run["finished"] and run["started"] >= since
        if done and need_password_run:
            done = any(r["app"] == "jellyfin" and r["step"] == "first run" and r["status"] != "skipped" for r in run["results"])
        if done:
            return st
        time.sleep(10)
    sys.exit("setup didn't finish in time")


RUN.mkdir()
(RUN / "data").mkdir()
(RUN / "override.yml").write_text(OVERRIDE)
(RUN / ".env").write_text(f"DATA={RUN / 'data'}\nPUID=1000\nPGID=1000\nTZ=Etc/UTC\n")
(RUN / "gluetun").mkdir()
(RUN / "gluetun" / "yeerr-vpn-off").write_text("live check: there's no VPN here\n")
print("building yeerr", flush=True)
sh("docker", "build", "-q", "-t", "yeerr:latest", str(REPO))
print("starting", RUN.name, flush=True)
dc("up", "-d")
for _ in range(120):
    try:
        if requests.get(f"{BASE}/api/health", timeout=3).ok:
            break
    except requests.RequestException:
        pass
    time.sleep(3)
else:
    print(dc("logs", "--tail", "40", "yeerr", check=False).stdout)
    sys.exit("yeerr never answered")

s = requests.Session()
t0 = time.time()
r = s.post(f"{BASE}/api/auth/setup", json={"name": NAME, "password": PASSWORD}, headers=H, timeout=30)
print("admin account:", r.status_code, flush=True)
s.headers.update(H)
first = wait_for_run(s, t0 - 3600, need_password_run=True)
t1 = time.time()
r = s.post(f"{BASE}/api/stack/setup", json={"password": PASSWORD}, timeout=30)
print("set up again:", r.status_code, r.text, flush=True)
second = wait_for_run(s, t1 - 1, need_password_run=False)


def show(label, st):
    print(f"--- {label}")
    for a in st["apps"]:
        print(f"  {a['label']:12} {'up' if a['up'] else 'DOWN':4} key={a['key']}")
    for x in st["run"]["results"]:
        print(f"  {x['status']:8} {x['app']:12} {x['step']:18} {x['seconds']:>6}s  {x['detail'][:140]}")


show("after the admin account was made", first)
show("second run", second)
bad = [x for x in first["run"]["results"] if x["status"] in ("failed", "skipped") and x["step"] != "indexers"]
changed = [x for x in second["run"]["results"] if x["status"] == "done"]
down = [a["label"] for a in second["apps"] if not a["up"]]
indexers = next(x for x in first["run"]["results"] if x["step"] == "indexers")

# from the outside: the things a person would notice
checks = {}
jf = sh("docker", "exec", f"{RUN.name}-sonarr-1", "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-X", "POST",
        "-H", "Content-Type: application/json", "-H", 'Authorization: MediaBrowser Client="t", Device="t", DeviceId="t", Version="1"',
        "-d", json.dumps({"Username": NAME, "Pw": PASSWORD}), "http://jellyfin:8096/Users/AuthenticateByName", check=False)
checks["Jellyfin takes your name and password"] = jf.stdout == "200"
qb = sh("docker", "exec", f"{RUN.name}-sonarr-1", "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "http://gluetun:8080/api/v2/app/version", check=False)
checks["qBittorrent answers at home without a login"] = qb.stdout == "200"
ui = sh("docker", "exec", f"{RUN.name}-radarr-1", "curl", "-s", "http://sonarr:8989/initialize.json", check=False)
checks["Sonarr's page needs no login at home"] = '"apiKey"' in ui.stdout
for k, v in checks.items():
    print(f"  {'✓' if v else '✗'} {k}")
ok = not bad and not changed and not down and indexers["status"] in ("done", "ok") and all(checks.values())
print("RESULT:", "ok" if ok else "problems", f"(first-run problems: {len(bad)}, second-run changes: {len(changed)}, down: {down})")
if "--keep" in sys.argv:
    print(f"left running for a look: docker compose -p {RUN.name} down")
else:
    dc("down", "-v", check=False)
sys.exit(0 if ok else 1)
