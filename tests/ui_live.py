"""Live streams in a browser, against stand-ins for YouTube's and Twitch's servers (made-up addresses; segments made
here with ffmpeg): a YouTube live plays in Parlor's player with the LIVE badge, going back turns it into Go live,
and Go live returns; Watch → Live lists who's live; a Twitch channel plays with its chat beside it; when Twitch runs
an ad, the ad's segments are never fetched and the hold card shows; past broadcasts and clips play; on a phone the
pages fit.

Run from app/: ../.venv/bin/python ../tests/ui_live.py   (prints RESULT: ok)"""
import asyncio
import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import live
import uvicorn
import websockets
import yt
from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="ui-live-"))
OUT.mkdir(parents=True, exist_ok=True)
SEG = Path(tempfile.mkdtemp(prefix="live-segs-"))
for name, src in (("live", "testsrc2=size=640x360:rate=30"), ("ad", "color=c=red:size=640x360:rate=30")):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", src, "-f", "lavfi", "-i", "sine=frequency=440", "-t", "20",
                    "-c:v", "libx264", "-preset", "ultrafast", "-g", "60", "-keyint_min", "60", "-sc_threshold", "0", "-c:a", "aac", "-f", "hls",
                    "-hls_time", "2", "-hls_list_size", "0", "-hls_segment_filename", str(SEG / f"{name}%d.ts"), str(SEG / f"{name}.m3u8")], check=True)
ADS = {"on": False}
FETCHED: list = []
START = time.time()


def window(twitch: bool) -> str:
    """a sliding live window over the ten made segments (two seconds each), 30 segments back for going back"""
    now = int((time.time() - START) / 2) + 30
    first = now - 30
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:2", f"#EXT-X-MEDIA-SEQUENCE:{first}",
             f"#EXT-X-DISCONTINUITY-SEQUENCE:{first // 10}"]
    for n in range(first, now):
        if n % 10 == 0 and n != first:
            lines.append("#EXT-X-DISCONTINUITY")
        ad = twitch and ADS["on"] and n >= now - 3
        if ad and n == now - 3:
            lines.append('#EXT-X-DATERANGE:ID="stitched-ad-1",CLASS="twitch-stitched-ad",START-DATE="2026-11-08T00:00:00Z",DURATION=6.0')
        lines += [f"#EXTINF:2.000,{'Amazon|1' if ad else 'live' if twitch else ''}", f"{'ad' if ad else 'live'}{n % 10}.ts"]
    return "\n".join(lines) + "\n"


MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=900000,RESOLUTION=640x360,CODECS="avc1.42c01e,mp4a.40.2"
{base}/media.m3u8
"""


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, body, kind="application/vnd.apple.mpegurl"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        p = urlparse(self.path).path
        FETCHED.append(p)
        twitch = "/tw/" in p or "/channel/hls/" in p or "/vod/" in p
        if p.endswith("/live.m3u8") or p.startswith("/api/channel/hls/") or p.startswith("/vod/"):
            base = "https://video-weaver.example.hls.ttvnw.net/tw" if twitch else "https://manifest.example.googlevideo.com/yt"
            return self.reply(200, MASTER.format(base=base))
        if p.endswith("/media.m3u8"):
            return self.reply(200, window(twitch))
        name = p.rsplit("/", 1)[-1]
        if name.endswith(".ts") and (SEG / name).is_file():
            return self.reply(200, (SEG / name).read_bytes(), "video/mp2t")
        if name.endswith(".mp4"):
            return self.reply(200, CLIP, "video/mp4")
        if name.endswith((".png", ".jpg")):
            return self.reply(200, PNG, "image/png")
        self.reply(404, "no")

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        q, v = body["query"], body["variables"]
        img = "https://static-cdn.jtvnw.net/pic.png"
        stream = {"title": "Speedrun practice", "viewersCount": 321, "createdAt": "2026-11-08T00:00:00Z", "game": {"name": "Chess"}, "previewImageURL": img}
        if "PlaybackAccessToken" in q:
            data = {"streamPlaybackAccessToken": {"value": "{}", "signature": "s"}, "videoPlaybackAccessToken": {"value": "{}", "signature": "s"}}
        elif "users(logins" in q:
            data = {"users": [{"login": x, "displayName": x.title(), "profileImageURL": img, "stream": stream if x == "speedy" else None} for x in v["l"]]}
        elif "searchFor" in q:
            data = {"searchFor": {"channels": {"edges": [{"item": {"login": "speedy", "displayName": "Speedy", "profileImageURL": img, "stream": stream}}]}}}
        elif "clip(slug" in q:
            data = {"clip": {"title": "Big moment", "durationSeconds": 3, "broadcaster": {"login": "speedy", "displayName": "Speedy"},
                             "playbackAccessToken": {"signature": "s", "value": "{}"}, "videoQualities": [{"quality": "720", "sourceURL": "https://clips.example.cloudfront.net/c.mp4"}]}}
        elif "user(login" in q:
            data = {"user": {"login": v["login"], "displayName": v["login"].title(), "description": "Practising runs every night.", "profileImageURL": img,
                             "stream": stream if v["login"] == "speedy" else None,
                             "videos": {"edges": [{"node": {"id": "1234567", "title": "Last night's runs", "lengthSeconds": 7200, "publishedAt": "2026-11-07T00:00:00Z",
                                                             "viewCount": 900, "previewThumbnailURL": img}}]},
                             "clips": {"edges": [{"node": {"slug": "BigMoment-abc", "title": "Big moment", "durationSeconds": 3, "viewCount": 50,
                                                            "createdAt": "2026-11-07T00:00:00Z", "thumbnailURL": img}}]}}}
        else:
            data = {}
        self.reply(200, json.dumps({"data": data}), "application/json")


PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000" "1f15c4890000000d49444154789c6360f8cfc0000003010100c9fe92ef0000000049454e44ae426082")
subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30", "-t", "3", "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(SEG / "clip.mp4")], check=True)
CLIP = (SEG / "clip.mp4").read_bytes()
up = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
threading.Thread(target=up.serve_forever, daemon=True).start()
UP = f"http://127.0.0.1:{up.server_port}"
_get, _post = live.requests.get, live.requests.post
live.requests.get = lambda url, **kw: _get(UP + urlparse(url).path + (f"?{urlparse(url).query}" if urlparse(url).query else ""), **kw)
live.requests.post = lambda url, **kw: _post(UP + "/gql", **kw)
yt.video_info = lambda vid, uid=None, fresh=False: {
    "id": vid, "title": "Rainy night radio", "channel": "Night Radio", "channel_id": "UC" + "x" * 22, "live_status": "is_live", "description": "",
    "formats": [{"manifest_url": "https://manifest.example.googlevideo.com/yt/live.m3u8", "protocol": "m3u8_native"}], "thumbnail": None}


async def irc(ws):  # Twitch's chat, standing in: one welcome, then a line every half second
    await ws.recv()
    n = 0
    try:
        while True:
            n += 1
            await ws.send(f"@color=#1E90FF;display-name=Viewer{n} :viewer{n}!v@v.tmi.twitch.tv PRIVMSG #speedy :nice run {n}")
            await asyncio.sleep(0.5)
    except websockets.ConnectionClosed:  # the page was left
        pass


def irc_server():
    async def main():
        async with websockets.serve(irc, "127.0.0.1", 0) as s:
            live.TW_CHAT = f"ws://127.0.0.1:{s.sockets[0].getsockname()[1]}"
            await asyncio.Future()
    asyncio.run(main())


threading.Thread(target=irc_server, daemon=True).start()
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    PORT = s.getsockname()[1]
BASE = f"http://127.0.0.1:{PORT}"
server = uvicorn.Server(uvicorn.Config(env.yeerr.app, host="127.0.0.1", port=PORT, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started or live.TW_CHAT.startswith("wss://"):
    time.sleep(0.05)
CHROME = next(iter(sorted(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux*/chrome"))), None)
checks, errors = {}, []


def device(b, width=1280, height=860):
    ctx = b.new_context(viewport={"width": width, "height": height}, color_scheme="dark", is_mobile=width < 500, has_touch=width < 500)
    p = ctx.new_page()
    p.on("pageerror", lambda e: errors.append(str(e)[:200]))
    p.goto(BASE + "/")
    p.wait_for_selector("#gateForm")
    p.fill("#gName", "admin")
    p.fill("#gPw", "correct horse")
    p.click("#gateForm .primary")
    p.wait_for_selector("#gateForm", state="detached", timeout=30000)
    return p


playing = "(() => { const v = document.querySelector('#video'); return v && v.currentTime > 2 && !v.paused && v.readyState >= 3; })()"
try:
    env.signed_in("admin")
    import core

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=str(CHROME) if CHROME else None, args=["--autoplay-policy=no-user-gesture-required"])
        p = device(b)
        # a YouTube live: Parlor's player, LIVE, back an hour, Go live
        p.goto(BASE + "/#/watch/abcdefghijk")
        p.wait_for_function(playing, timeout=30000)
        checks["a YouTube live plays in Parlor's player with LIVE"] = p.inner_text(".livebadge") == "LIVE"
        p.evaluate("const v = document.querySelector('#video'); v.currentTime = Math.max(0, v.currentTime - 40)")
        p.wait_for_function("document.querySelector('.livebadge').classList.contains('behind')", timeout=10000)
        behind = p.inner_text(".livebadge")
        p.click(".livebadge")
        p.wait_for_function("!document.querySelector('.livebadge').classList.contains('behind')", timeout=10000)
        checks["going back shows how far, and Go live returns"] = "Go live" in behind
        p.screenshot(path=str(OUT / "youtube-live.png"))
        # Watch → Live: follow a Twitch channel found by search; who's live
        p.goto(BASE + "/#/live")
        p.wait_for_selector("#twName", timeout=20000)
        p.fill("#twName", "speed")
        p.wait_for_selector("[data-follow=speedy]", timeout=10000)
        p.click("[data-follow=speedy]")
        p.wait_for_selector("[data-unfollow=speedy]", timeout=10000)
        checks["Twitch: found by search, followed, and shown live"] = p.locator(".grid .tcard", has_text="Speedrun practice").count() == 1
        p.screenshot(path=str(OUT / "live.png"), full_page=True)
        # the channel: live with chat beside it
        p.click(".grid .tcard")
        p.wait_for_function(playing, timeout=30000)
        p.wait_for_function("document.querySelectorAll('#chat li').length >= 3", timeout=15000)
        checks["a Twitch channel plays, chat beside it"] = "nice run" in p.inner_text("#chat")
        # an ad starts: its segments are never fetched, the hold card shows; it ends: the card goes
        FETCHED.clear()
        ADS["on"] = True
        p.wait_for_function("!document.querySelector('.livehold').hidden", timeout=20000)
        p.wait_for_timeout(4000)
        ad_fetched = [x for x in FETCHED if "/ad" in x]
        p.screenshot(path=str(OUT / "twitch-ad-hold.png"))
        ADS["on"] = False
        p.wait_for_function("document.querySelector('.livehold').hidden", timeout=20000)
        checks["during an ad: never fetched, the hold card shows, then it's back"] = not ad_fetched
        p.screenshot(path=str(OUT / "twitch.png"))
        # a past broadcast and a clip
        p.click("text=Last night's runs")
        p.wait_for_function(playing, timeout=30000)
        checks["a past broadcast plays, without the LIVE badge"] = p.locator(".livebadge").count() == 0
        p.goto(BASE + "/#/live/tw/speedy/clip/BigMoment-abc")
        p.wait_for_function("(() => { const v = document.querySelector('#video'); return v && v.currentTime > 0.5; })()", timeout=20000)
        checks["a clip plays"] = True
        # an offline channel; a phone
        p.goto(BASE + "/#/live/tw/napper")
        p.wait_for_selector(".toff", timeout=10000)
        checks["an offline channel says so and lists its broadcasts"] = "offline" in p.inner_text(".toff") and p.locator(".tcard").count() == 2
        phone = device(b, 390, 844)
        for path in ("/#/live", "/#/live/tw/speedy"):
            phone.goto(BASE + path)
            phone.wait_for_timeout(2500)
            if phone.evaluate("document.documentElement.scrollWidth > innerWidth + 1"):
                errors.append(f"sideways scroll on a phone at {path}")
        phone.screenshot(path=str(OUT / "twitch-phone.png"), full_page=True)
        checks["on a phone the pages fit"] = not any("sideways" in e for e in errors)
        b.close()
except Exception as exc:  # report what was reached
    import traceback

    errors.append(f"{type(exc).__name__}: {str(exc)[:200]} at line {traceback.extract_tb(exc.__traceback__)[-1].lineno}")
finally:
    server.should_exit = True

for k, v in checks.items():
    print(f"  {'✓' if v else '✗'} {k}")
print("page errors:", errors or "none")
print("screenshots:", OUT)
ok = len(checks) == 9 and all(checks.values()) and not errors
print("RESULT:", "ok" if ok else "FAIL")
sys.exit(0 if ok else 1)
