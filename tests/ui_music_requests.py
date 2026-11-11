"""Music requests in a real browser: Request → Music lists your requests with their state and why (searching,
downloading, getting it from YouTube, stuck, pick the right album, arrived); picking a Spotify album starts the
YouTube download, Retry and Get it from YouTube work. Lidarr and music-fndr are fakes; no page errors; screenshots
at desktop and phone width:
   python tests/ui_music_requests.py [OUT_DIR]   (needs Playwright and its Chromium)"""
import json
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import arr
import auth
import core
import music
import musicreq
import uvicorn
from playwright.sync_api import sync_playwright
from test_music_requests import KEY, FakeLidarr, album

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="ui-music-requests-"))
OUT.mkdir(parents=True, exist_ok=True)
PASSWORD = "Ui-music-req-pass-42!"  # pragma: allowlist secret (made up)
COVER = ("data:image/svg+xml," + "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1 1'%3E%3Cdefs%3E%3ClinearGradient id='g' x2='1' y2='1'%3E"
         "%3Cstop stop-color='%23ffb547'/%3E%3Cstop offset='1' stop-color='%236b5cff'/%3E%3C/linearGradient%3E%3C/defs%3E"
         "%3Crect width='1' height='1' fill='url(%23g)'/%3E%3C/svg%3E")
lidarr, jobs = FakeLidarr(), []
root = env.TMP / "ui-music-stack"
(root / "lidarr").mkdir(parents=True, exist_ok=True)
(root / "lidarr" / "config.xml").write_text(f"<Config><ApiKey>{KEY}</ApiKey></Config>")


def queue_download(kind, id, title=None, subtitle=None, image=None, output=None):
    jobs.append({"job": f"j{len(jobs) + 1}", "id": id, "output": output, "status": "queued", "songs": {}})
    return jobs[-1]


for p in (mock.patch.object(arr, "STACK", root), mock.patch.object(arr.requests, "request", lidarr), mock.patch.object(musicreq, "start", lambda: None),
          mock.patch.object(music, "queue_download", queue_download), mock.patch.object(musicreq, "STAGING", env.TMP / "staging"),
          mock.patch.object(music, "search", lambda q: {"albums": [{"id": "spQ" + "x" * 19, "name": "Quiet Hours", "artists": [{"name": "Band"}], "year": "1997"}]}),
          mock.patch.object(music, "album", lambda sid: {"total": 10})):
    p.start()
arr._keys.clear()
core.db()
uid = auth.create_user("listener", PASSWORD, False)
now = time.time()
seed = [  # title, artist, state, why, via, candidates
    ("Quiet Hours", "The Lamps", "searching", "", "lidarr", None),
    ("Harbour Songs", "Fog Bell", "downloading", "", "lidarr", None),
    ("Night Train", "Ada Brook", "fetching", "Lidarr found nothing in 6 hours", "youtube", None),
    ("Low Tide", "Fog Bell", "needs_look", "Lidarr found nothing in 6 hours. More than one album on Spotify could be it: pick it", "youtube",
     [{"id": "spA" + "x" * 19, "name": "Low Tide", "artist": "Fog Bell", "year": "2019", "tracks": 10, "image": COVER},
      {"id": "spB" + "x" * 19, "name": "Low Tide (Deluxe)", "artist": "Fog Bell", "year": "2020", "tracks": 14, "image": COVER}]),
    ("Ledger", "The Keeper", "failed", "Album match is not close enough: 62%. The files are in parlor-music/request-5; Retry imports them again", "youtube", None),
    ("First Light", "The Lamps", "imported", "", "lidarr", None),
]
for n, (title, by, state, why, via, picks) in enumerate(seed, 1):
    lidarr.albums[n] = album(n, title)
    core.db().execute("INSERT INTO music_requests(user_id, album, title, artist_name, image, state, why, via, created, updated, searched, candidates, spotify) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (uid, n, title, by, COVER, state, why, via, now - n * 3600, now - n * 60, now - n * 3600, json.dumps(picks) if picks else None,
                       "spC" + "x" * 19 if state == "failed" else None))

(env.TMP / "staging" / "request-5" / "The Keeper" / "Ledger").mkdir(parents=True, exist_ok=True)  # what YouTube left for Lidarr
(env.TMP / "staging" / "request-5" / "The Keeper" / "Ledger" / "01 - One.mp3").write_bytes(b"ID3")

with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    PORT = s.getsockname()[1]
server = uvicorn.Server(uvicorn.Config(env.yeerr.app, host="127.0.0.1", port=PORT, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.1)
BASE = f"http://127.0.0.1:{PORT}"
SHELL = next(iter(sorted(Path.home().glob(".cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"))), None)
errors, checks = [], {}

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=str(SHELL) if SHELL else None)
    ctx = b.new_context(viewport={"width": 1366, "height": 1000}, color_scheme="dark")
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(BASE + "/")
    page.wait_for_selector("#gateForm")
    page.fill("#gName", "listener")
    page.fill("#gPw", PASSWORD)
    page.click("#gateForm .primary")
    page.wait_for_selector("#gateForm", state="detached", timeout=30000)
    page.goto(BASE + "/#/lidarr")
    page.wait_for_selector(".mreqs li", timeout=30000)
    text = page.inner_text("#musicReqs")
    checks["each request shows its state"] = all(w in text for w in ("Lidarr is searching", "Downloading in Lidarr", "Getting it from YouTube",
                                                                         "Pick the right album", "Stuck", "In your library"))
    checks["and why"] = "not close enough" in text and "via YouTube" in text
    page.wait_for_timeout(600)
    page.screenshot(path=str(OUT / "requests-desk.png"), full_page=True)
    page.click("[data-mreq=choose][data-spotify^=spB]")
    page.wait_for_function("document.querySelector('#musicReqs').innerText.split('Getting it from YouTube').length > 2", timeout=30000)
    checks["picking an album starts the YouTube download"] = jobs[-1]["id"].startswith("spB") and "request-4" in jobs[-1]["output"]
    page.click(".mreq.failed [data-mreq=retry]")
    page.wait_for_function("!document.querySelector('.mreq.failed')", timeout=30000)
    checks["Retry imports again"] = lidarr.commands and list(lidarr.commands.values())[-1]["name"] == "ManualImport"
    page.click(".mreq.searching [data-mreq=youtube]")
    page.wait_for_function("!document.querySelector('.mreq.searching [data-mreq=youtube]')", timeout=30000)
    checks["Get it from YouTube hands it over now"] = lidarr.albums[1]["monitored"] is False and jobs[-1]["id"].startswith("spQ")
    phone = b.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark", is_mobile=True)
    phone.add_cookies(ctx.cookies())
    pp = phone.new_page()
    pp.on("pageerror", lambda e: errors.append(str(e)))
    pp.goto(BASE + "/#/lidarr")
    pp.wait_for_selector(".mreqs li", timeout=30000)
    pp.wait_for_timeout(600)
    pp.screenshot(path=str(OUT / "requests-phone.png"), full_page=True)
    checks["no sideways scroll on a phone"] = pp.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    b.close()
server.should_exit = True
for k, v in checks.items():
    print(f"  {'✓' if v else '✗'} {k}")
print("page errors:", errors or "none")
shots = sorted(x.name for x in OUT.glob("*.png"))
print("screenshots:", ", ".join(shots))
ok = len(checks) == 6 and all(checks.values()) and not errors and len(shots) == 2
print("RESULT:", "ok" if ok else "FAIL")
sys.exit(0 if ok else 1)
