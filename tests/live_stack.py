"""End-to-end check of Parlor's own compose: a brand-new folder, the real docker-compose.yml (Gluetun with
Parlor's front door switched to "VPN off", since there's no VPN here; no published ports), Parlor built
from this repo. Create the admin account over HTTP, wait for the automatic setup, check every app, then
run setup again: it must change nothing. Exit 0 when all of that holds. The stack is removed at the end
(--keep leaves it running for a look). Needs Docker and internet: python tests/live_stack.py [--keep]
--only questarr judges only Questarr's part (the whole stack still runs: it needs Prowlarr and qBittorrent). A game is
searched by title there with no IGDB keys of anyone's: Parlor answers Questarr from Steam's store."""
import json
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[1]
RUN = Path(tempfile.mkdtemp(prefix="parlor-")) / f"e2e-{int(time.time())}"  # fresh every time
NAME, PASSWORD = "stackadmin", "Live-setup-pass-42!"  # pragma: allowlist secret (a made-up password for a throwaway stack)
with socket.socket() as _s:  # a free port, so a stack left running from an earlier run doesn't get in the way
    _s.bind(("127.0.0.1", 0))
    PORT = _s.getsockname()[1]
BASE, H = f"http://127.0.0.1:{PORT}", {"X-Requested-With": "parlor"}
OVERRIDE = """
services:
  parlor:
    build: !reset null
    ports: !override ["127.0.0.1:{PORT}:8800"]
    environment:
      PUBLIC_INDEXERS: yts,eztv,thepiratebay,nyaasi,knaben
      UPDATE_HOURS: "0"
  setup:
    build: !reset null
  gluetun:            # the real one; its front door is told "VPN off" below, as there's no VPN file here
    ports: !reset []
""".replace("{PORT}", str(PORT)) + "".join(f"  {s}:\n    ports: !reset []\n" for s in ("prowlarr", "flaresolverr", "sonarr", "radarr", "lidarr", "bazarr", "jellyfin", "seerr", "cleanuparr", "questarr", "checkrr"))


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
(RUN / "gluetun" / "vpn-off").write_text("live check: there's no VPN here\n")
print("building parlor", flush=True)
sh("docker", "build", "-q", "-t", "parlor:latest", str(REPO))
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
    print(dc("logs", "--tail", "40", "parlor", check=False).stdout)
    sys.exit("Parlor never answered")

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
changed = [x for x in second["run"]["results"] if x["status"] == "done" and x["step"] != "indexers"]  # public sites come and go
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
ln = sh("docker", "exec", "-u", "abc", f"{RUN.name}-radarr-1", "sh", "-c", "mkdir -p /data/downloads /data/movies && touch /data/downloads/.ln "
        "&& ln /data/downloads/.ln /data/movies/.ln && rm /data/downloads/.ln /data/movies/.ln", check=False)
checks["Radarr can hardlink a download into the library (one /data mount)"] = ln.returncode == 0


def quest(method, path, body=None, token=None):
    """Questarr's own API, asked from the container next door (its port isn't published here)"""
    cmd = ["docker", "exec", f"{RUN.name}-sonarr-1", "curl", "-s", "-m", "90", "-X", method, "-H", "Content-Type: application/json"]
    cmd += ["-H", f"Authorization: Bearer {token}"] if token else []
    cmd += ["-d", json.dumps(body)] if body is not None else []
    out = sh(*cmd, f"http://questarr:5000{path}", check=False).stdout
    try:
        return json.loads(out)
    except ValueError:
        return {"error": out[:200]}


image = json.loads(dc("config", "--format", "json").stdout)["services"]["questarr"]["image"]
checks["Questarr's image is the official one, pinned by digest"] = image.startswith("ghcr.io/doezer/questarr:") and "@sha256:" in image
token = quest("POST", "/api/auth/login", {"username": NAME, "password": PASSWORD}).get("token")
checks["Questarr takes your name and password"] = bool(token)
clients = quest("GET", "/api/downloaders", token=token) if token else []
qb_ = next((d for d in clients if isinstance(d, dict) and d.get("type") == "qbittorrent"), None)
checks["Questarr has qBittorrent (behind the VPN's container) as its download client"] = bool(qb_) and qb_["url"] == "http://gluetun" and qb_["category"] == "games"
tested = quest("POST", f"/api/downloaders/{qb_['id']}/test", token=token) if qb_ else {}
print("  Questarr's test of qBittorrent:", json.dumps(tested)[:200])
checks["Questarr's own test of qBittorrent passes"] = tested.get("success") is True
feeds = quest("GET", "/api/indexers", token=token) if token else []
print("  Questarr's indexers:", ", ".join(f"{i.get('name')} ({i.get('url')})" for i in feeds if isinstance(i, dict))[:400])
checks["Questarr has Prowlarr's indexers as Torznab feeds"] = bool(feeds) and all("prowlarr:9696" in (i.get("url") or "") for i in feeds)
checks["Questarr's games go to /data/games"] = (quest("GET", "/api/imports/config", token=token) or {}).get("libraryRoot") == "/data/games"
found = {}
for word in ("doom", "linux", "2024"):  # a raw search of the indexers: no IGDB in it. Public sites come and go, so a few words
    found = quest("GET", f"/api/search?query={word}", token=token) if token else {}
    print(f"  Questarr's search for {word!r}: {len(found.get('items') or [])} results, errors: {str(found.get('errors'))[:200]}")
    if found.get("items"):
        break
checks["a search through Questarr returns results from the indexers (no IGDB)"] = bool(found.get("items"))
row = next(a for a in second["apps"] if a["app"] == "questarr")
# a game by its title, on this clean stack with no keys of anyone's: Questarr asks "IGDB", and Parlor answers from Steam's store
games = quest("GET", "/api/igdb/search?q=portal&limit=5", token=token)
print("  a title search for portal:", [g.get("title") for g in games][:5] if isinstance(games, list) else games)
checks["with no IGDB keys, Questarr finds a game by its title"] = isinstance(games, list) and any("portal" in (g.get("title") or "").lower() and g.get("coverUrl", "").startswith("https://") for g in games)
game = quest("GET", f"/api/igdb/game/{games[0]['igdbId']}", token=token) if isinstance(games, list) and games else {}
checks["Questarr opens that game's own page: summary, cover, release date"] = bool(game.get("title") and game.get("summary") and game.get("coverUrl") and game.get("releaseDate"))
config = quest("GET", "/api/config", token=token)
checks["Questarr counts itself set up, and Services asks for no keys"] = (config.get("igdb") or {}).get("configured") is True and "needs" not in row
without = requests.post(f"{BASE}/lookup/games/v4/games", data='search "portal"; fields name; limit 5;', timeout=30)
checks["nobody but Questarr gets an answer from Parlor's lookup"] = without.status_code == 401
if "--only" in sys.argv:  # judge one app's part
    only = sys.argv[sys.argv.index("--only") + 1]
    bad, changed = [x for x in bad if x["app"] == only], [x for x in changed if x["app"] == only]
    down = [d for d in down if d.lower() == only]
    checks = {k: v for k, v in checks.items() if only in k.lower()}
    assert checks, f"no checks for {only}"
    lists = [x for x in first["run"]["results"] if x["app"] == only and x["step"] == "indexers"]
    indexers = lists[0] if lists else indexers
for k, v in checks.items():
    print(f"  {'✓' if v else '✗'} {k}")
ok = not bad and not changed and not down and indexers["status"] in ("done", "ok") and all(checks.values())
print("RESULT:", "ok" if ok else "problems", f"(first-run problems: {len(bad)}, second-run changes: {len(changed)}, down: {down})")
if "--keep" in sys.argv:
    print(f"left running for a look: docker compose -p {RUN.name} down")
else:
    dc("down", "-v", check=False)
sys.exit(0 if ok else 1)
