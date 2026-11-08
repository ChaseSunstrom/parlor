"""Live streams against the real services (needs the network): a YouTube live (Lofi Girl's, which is always on) and
the busiest Twitch stream each play for 30 s in a real browser through Parlor; going back on YouTube works; every
Twitch playlist Parlor served had its ad segments turned into gaps (and says how many it saw).

Run from app/: ../.venv/bin/python ../tests/live_streams.py   (prints RESULT: ok)"""
import socket
import sys
import threading
import time
from pathlib import Path

import requests

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import live
import uvicorn
import yt
from playwright.sync_api import sync_playwright

served: list = []
_rewrite = live.rewrite


def watched(text, base):
    out = _rewrite(text, base)
    if "ttvnw.net" in base or "live-video.net" in base:
        served.append(out)
    return out


live.rewrite = watched
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

try:
    env.signed_in("admin")
    yt_live = yt.extract("https://www.youtube.com/@LofiGirl/live", noplaylist=True)["id"]
    top = requests.post(live.TW_GQL, headers={"Client-ID": live.TW_CLIENT}, timeout=15,
                        json={"query": "query { streams(first: 1) { edges { node { broadcaster { login } } } } }"}).json()
    tw_live = top["data"]["streams"]["edges"][0]["node"]["broadcaster"]["login"]
    print(f"YouTube live {yt_live}, Twitch {tw_live}")
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=str(CHROME) if CHROME else None, args=["--autoplay-policy=no-user-gesture-required"])
        p = b.new_context(viewport={"width": 1280, "height": 860}).new_page()
        p.on("pageerror", lambda e: errors.append(str(e)[:200]))
        p.goto(BASE + "/")
        p.wait_for_selector("#gateForm")
        p.fill("#gName", "admin")
        p.fill("#gPw", "correct horse")
        p.click("#gateForm .primary")
        p.wait_for_selector("#gateForm", state="detached", timeout=30000)
        for name, path in (("YouTube", f"/#/watch/{yt_live}"), ("Twitch", f"/#/live/tw/{tw_live}")):
            p.goto(BASE + path)
            p.wait_for_function("(() => { const v = document.querySelector('#video'); return v && v.currentTime > 1 && v.readyState >= 3; })()", timeout=60000)
            t0 = p.evaluate("document.querySelector('#video').currentTime")
            p.wait_for_timeout(30000)
            t1 = p.evaluate("document.querySelector('#video').currentTime")
            print(f"{name}: {t1 - t0:.1f} s played in 30 s")
            checks[f"{name} live plays through Parlor for 30 s"] = t1 - t0 > 20
            if name == "YouTube":
                p.evaluate("const v = document.querySelector('#video'); v.currentTime = v.currentTime - 120")
                p.wait_for_function("document.querySelector('.livebadge').classList.contains('behind')", timeout=15000)
                p.wait_for_timeout(3000)
                checks["YouTube: going back two minutes plays"] = p.evaluate("!document.querySelector('#video').paused && document.querySelector('#video').readyState >= 3")
        b.close()
    # the rest against the real services, through Parlor's API: search, a channel's broadcasts and clips, a clip's
    # file, a broadcast's playlist, and a YouTube subscription's live found from its channel page
    s = requests.Session()
    s.post(f"{BASE}/api/auth/login", json={"name": "admin", "password": "correct horse"}, headers=env.H)
    found = s.get(f"{BASE}/api/live/tw/search", params={"q": "lofi"}).json()["channels"]
    ch = s.get(f"{BASE}/api/live/tw/channel/{tw_live}").json()
    clip = s.get(f"{BASE}/api/live/tw/clip/{ch['clips'][0]['slug']}").json() if ch["clips"] else {}
    clip_file = s.get(BASE + clip["src"], headers={"Range": "bytes=0-1023"}) if clip else None
    vod = s.get(f"{BASE}/api/live/tw/vod/{ch['videos'][0]['id']}.m3u8") if ch["videos"] else None
    print(f"search: {len(found)} channels; {tw_live}: {len(ch['videos'])} broadcasts, {len(ch['clips'])} clips; clip file {clip_file and clip_file.status_code}, broadcast {vod and vod.status_code}")
    checks["Twitch search, broadcasts and clips answer, and a clip's file comes through Parlor"] = (
        bool(found) and clip_file is not None and clip_file.status_code in (200, 206) and clip_file.headers.get("content-type", "").startswith("video"))
    checks["a past broadcast's playlist comes through Parlor"] = vod is not None and vod.status_code == 200 and "/api/live/pl?u=" in vod.text
    s.post(f"{BASE}/api/me/subs", json={"channel_id": "UCSJ4gkVC6NrvII8umztf0Ow", "name": "Lofi Girl"}, headers=env.H)
    now = s.get(f"{BASE}/api/live/now").json()
    checks["a subscription that's live shows in Live now"] = yt_live in [v["id"] for v in now["youtube"]]
    ads = sum(t.count("#EXT-X-GAP") for t in served)
    leaked = []
    for t in served:
        lines = t.splitlines()
        leaked += [line for i, line in enumerate(lines) if line.startswith("#EXTINF") and not line.rstrip().endswith(",live") and lines[i + 1] != "#EXT-X-GAP"]
    print(f"Twitch playlists served: {len(served)}, ad segments turned into gaps: {ads}" + ("" if ads else " (no ad ran while watching: the ad path is covered by test_live.py and ui_live.py)"))
    checks["no Twitch ad segment reached the browser"] = bool(served) and not leaked
except Exception as exc:  # report what was reached
    import traceback

    errors.append(f"{type(exc).__name__}: {str(exc)[:200]} at line {traceback.extract_tb(exc.__traceback__)[-1].lineno}")
finally:
    server.should_exit = True

for k, v in checks.items():
    print(f"  {'✓' if v else '✗'} {k}")
print("page errors:", errors or "none")
ok = len(checks) == 7 and all(checks.values()) and not errors
print("RESULT:", "ok" if ok else "FAIL")
sys.exit(0 if ok else 1)
