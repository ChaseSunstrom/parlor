"""Games → Play from your PC, live: a gaming PC (Sunshine on a virtual screen, software encoding, no GPU: the
stand-in in tests/sunshine-standin) and the Moonlight Web bridge run in Docker; Parlor (served here) puts the bridge
behind its sign-in. A browser opens the bridge through Parlor with no second login, adds the PC, pairs it with
Sunshine's PIN, and plays its screen over WebRTC. Nobody signed out, without Games, or on another site gets in.

Run from app/: ../.venv/bin/python ../tests/live_games_stream.py   (needs Docker; prints RESULT: ok)"""
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests
import urllib3

HERE = Path(__file__).resolve().parent
NET, PC, BRIDGE = "parlor-games-check", "parlor-games-pc", "parlor-games-bridge"
SUNSHINE_LOGIN = ("parlor", "standin-pass")  # the stand-in's own, made up in its run.sh
urllib3.disable_warnings()


def sh(*cmd, check=True):
    return subprocess.run(cmd, check=check, capture_output=True, text=True).stdout.strip()


def ip(name):
    return sh("docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", name)


def down():
    sh("docker", "rm", "-f", PC, BRIDGE, check=False)
    sh("docker", "network", "rm", NET, check=False)


down()
sh("docker", "build", "-q", "-t", "parlor-sunshine-standin", str(HERE / "sunshine-standin"))
sh("docker", "network", "create", NET)
sh("docker", "run", "-d", "--name", PC, "--network", NET, "parlor-sunshine-standin")
sh("docker", "run", "-d", "--name", BRIDGE, "--network", NET, "-e", "FORWARDED_HEADER=X-Parlor-User", "-e", "PATH_PREFIX=/games/stream",
   "-e", "WEBRTC_PORT_RANGE=40000:40010", "mrcreativ3001/moonlight-web-stream:v3.0.1")
PC_IP, BRIDGE_IP = ip(PC), ip(BRIDGE)
for _ in range(120):  # Sunshine's PIN page, then the bridge
    try:
        requests.get(f"https://{PC_IP}:47990/api/pin", auth=SUNSHINE_LOGIN, verify=False, timeout=2)
        requests.get(f"http://{BRIDGE_IP}:8080/games/stream/", timeout=2)
        break
    except requests.RequestException:
        time.sleep(0.5)

os.environ["GAMES_STREAM_URL"] = f"http://{BRIDGE_IP}:8080"
import yeerr_test_env as env  # noqa: E402  (a throwaway data folder, then the app)

import core  # noqa: E402
import games  # noqa: E402
import uvicorn  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

games.STREAM = os.environ["GAMES_STREAM_URL"]
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    PORT = s.getsockname()[1]
BASE = f"http://127.0.0.1:{PORT}"
server = uvicorn.Server(uvicorn.Config(env.yeerr.app, host="127.0.0.1", port=PORT, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.05)
CHROME = next(iter(sorted(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux*/chrome"))), None)
checks, errors = {}, []


def session(name, admin=False):
    import auth

    if not core.one("SELECT 1 AS x FROM users WHERE name = ?", name):
        auth.create_user(name, "correct horse", admin)
    s = requests.Session()
    assert s.post(f"{BASE}/api/auth/login", json={"name": name, "password": "correct horse"}, headers=env.H).status_code == 200
    return s


try:
    env.signed_in("admin")
    # who may: a member waits until an admin has opened it once; signed out, no Games, another site: refused
    member = session("gamer")
    first = member.get(f"{BASE}/games/stream/")
    checks["a member waits for an admin to set the bridge up"] = first.status_code == 409 and "admin" in first.json()["detail"]
    checks["signed out: refused"] = requests.get(f"{BASE}/games/stream/").status_code == 401
    boss = session("admin")
    page = boss.get(f"{BASE}/games/stream/")
    checks["an admin opens the bridge through Parlor, no second login"] = page.status_code == 200 and "Moonlight" in page.text
    core.db().execute("UPDATE users SET blocked = 'games' WHERE name = 'gamer'")
    checks["without Games: refused"] = member.get(f"{BASE}/games/stream/").status_code == 403
    core.db().execute("UPDATE users SET blocked = '' WHERE name = 'gamer'")
    checks["a member may once an admin has"] = member.get(f"{BASE}/games/stream/").status_code == 200
    cross = boss.post(f"{BASE}/games/stream/api/host", json={"address": PC_IP}, headers={"Origin": "https://evil.example"})
    checks["a change from another site: refused"] = cross.status_code == 403
    spoof = member.get(f"{BASE}/games/stream/api/user", headers={"X-Parlor-User": "parlor-1"}).text
    print("the bridge says the member is:", spoof[:120])
    checks["who's asking is Parlor's to say, never the browser's"] = "parlor-2" in spoof and "parlor-1" not in spoof

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=str(CHROME) if CHROME else None,
                               args=["--autoplay-policy=no-user-gesture-required", "--disable-features=WebRtcHideLocalIpsWithMdns"])
        ctx = b.new_context(viewport={"width": 1280, "height": 800})
        ctx.add_cookies([{"name": core.COOKIE, "value": boss.cookies[core.COOKIE], "url": BASE}])
        p = ctx.new_page()
        p.on("pageerror", lambda e: errors.append(str(e)))
        p.goto(f"{BASE}/games/stream/")
        p.wait_for_selector(".host-add", timeout=20000)
        p.click(".host-add")
        p.wait_for_function("[...document.querySelectorAll('input')].some((i) => i.offsetParent)", timeout=10000)
        [i for i in p.query_selector_all("input") if i.is_visible()][0].fill(PC_IP)
        p.get_by_text("Ok", exact=True).click()
        p.wait_for_selector(".host-element", timeout=20000)
        # pairing: the bridge shows a PIN (its answer streams through Parlor while it waits), entered on the PC
        p.locator(".host-element:visible").first.click()
        p.wait_for_function("/Pin: \\d{4}/.test(document.body.innerText)", timeout=20000)
        pin = re.search(r"Pin: (\d{4})", p.inner_text("body"))[1]
        for _ in range(40):  # the bridge shows the PIN a moment before it asks the PC
            pending = requests.get(f"https://{PC_IP}:47990/api/pin", auth=SUNSHINE_LOGIN, verify=False).json()["pairings"]
            if pending:
                break
            time.sleep(0.25)
        done = requests.post(f"https://{PC_IP}:47990/api/pin", auth=SUNSHINE_LOGIN, verify=False,
                             json={"pairing_id": pending[0]["id"], "pin": pin, "name": "parlor"}).json()
        p.wait_for_function("!/Pin: \\d{4}/.test(document.body.innerText)", timeout=20000)
        checks["paired with Sunshine's PIN, through Parlor"] = done.get("status") is True
        for ok in [e for e in p.query_selector_all("button") if e.is_visible() and e.inner_text().strip() == "Ok"]:
            ok.click()
        # play: the PC's screen arrives as video, over WebRTC
        p.locator(".host-element:visible").first.click()
        p.wait_for_selector(".app-element:visible", timeout=20000)
        p.locator(".app-element:visible").first.click()
        p.wait_for_timeout(2000)
        p = ctx.pages[-1]  # the stream opens in a tab of its own
        p.wait_for_function("(() => { const v = document.querySelector('video'); return v && v.videoWidth > 0 && v.currentTime > 3; })()", timeout=60000)
        t1 = p.evaluate("document.querySelector('video').currentTime")
        p.wait_for_timeout(2000)
        t2, size = p.evaluate("[document.querySelector('video').currentTime, document.querySelector('video').videoWidth + 'x' + document.querySelector('video').videoHeight]")
        p.screenshot(path=str(Path(env.TMP) / "stream.png"))
        logs = subprocess.run(["docker", "logs", BRIDGE], capture_output=True, text=True)
        rtc = "state=connected" in logs.stdout + logs.stderr
        print(f"stream: {size}, {t2 - t1:.1f} s played in 2 s, WebRTC {'connected' if rtc else 'not used'}; screenshot {env.TMP}/stream.png")
        checks["the PC's screen plays in the browser, over WebRTC"] = t2 - t1 > 1.5 and rtc
        b.close()
except Exception as exc:  # report what was reached
    import traceback

    errors.append(f"{type(exc).__name__}: {exc} at line {traceback.extract_tb(exc.__traceback__)[-1].lineno}"[:300])
finally:
    server.should_exit = True
    down()

for k, v in checks.items():
    print(f"  {'✓' if v else '✗'} {k}")
print("errors:", errors or "none")
ok = len(checks) == 9 and all(checks.values()) and not errors
print("RESULT:", "ok" if ok else "FAIL")
sys.exit(0 if ok else 1)
