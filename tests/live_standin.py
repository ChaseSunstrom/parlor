"""Stand-ins for YouTube's and Twitch's servers, for the browser checks and the README's screenshots (made-up
addresses; segments made here with ffmpeg): a live YouTube video "abcdefghijk", the Twitch channel "speedy" (live,
with a past broadcast and a clip) and "napper" (offline), and Twitch's chat. Importing this starts them and points
live.py (and yt.video_info) at them; ADS["on"] makes Twitch run an ad, FETCHED lists what was asked. The README's
screenshots dress them up: more live channels (STREAMS), pictures by file name (IMAGES), chat lines (CHAT) and a
picture to stream (make_segments)."""
import asyncio
import json
import subprocess
import tempfile
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import requests

import live
import websockets
import yt

SEG = Path(tempfile.mkdtemp(prefix="live-segs-"))


def make_segments(name, source):
    """ten two-second segments, NAME0.ts…NAME9.ts, from an ffmpeg input (a lavfi pattern, or a looped picture)"""
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *source, "-f", "lavfi", "-i", "sine=frequency=440", "-t", "20", "-vf", "scale=640:360,format=yuv420p",
                    "-r", "30", "-c:v", "libx264", "-preset", "ultrafast", "-g", "60", "-keyint_min", "60", "-sc_threshold", "0", "-c:a", "aac", "-f", "hls",
                    "-hls_time", "2", "-hls_list_size", "0", "-hls_segment_filename", str(SEG / f"{name}%d.ts"), str(SEG / f"{name}.m3u8")], check=True)


make_segments("live", ["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30"])
make_segments("ad", ["-f", "lavfi", "-i", "color=c=red:size=640x360:rate=30"])
ADS = {"on": False}
STREAMS = {"speedy": {"title": "Speedrun practice", "viewersCount": 321, "game": "Chess"}}  # who's live
IMAGES: dict = {}  # picture file name -> PNG bytes (otherwise one blank pixel)
CHAT = [("Viewer{n}", "#1E90FF", "nice run {n}")]  # (name, colour, text), taken in turn
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
            return self.reply(200, IMAGES.get(name, PNG), "image/png")
        self.reply(404, "no")

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        q, v = body["query"], body["variables"]
        def img(login, kind):
            return f"https://static-cdn.jtvnw.net/{login}-{kind}.png"

        def stream(login):
            s = STREAMS.get(login)
            return s and {"title": s["title"], "viewersCount": s["viewersCount"], "createdAt": "2026-11-08T00:00:00Z", "game": {"name": s["game"]},
                          "previewImageURL": img(login, "preview")}
        if "PlaybackAccessToken" in q:
            data = {"streamPlaybackAccessToken": {"value": "{}", "signature": "s"}, "videoPlaybackAccessToken": {"value": "{}", "signature": "s"}}
        elif "users(logins" in q:
            data = {"users": [{"login": x, "displayName": x.title(), "profileImageURL": img(x, "avatar"), "stream": stream(x)} for x in v["l"]]}
        elif "searchFor" in q:
            data = {"searchFor": {"channels": {"edges": [{"item": {"login": "speedy", "displayName": "Speedy", "profileImageURL": img("speedy", "avatar"), "stream": stream("speedy")}}]}}}
        elif "clip(slug" in q:
            data = {"clip": {"title": "Big moment", "durationSeconds": 3, "broadcaster": {"login": "speedy", "displayName": "Speedy"},
                             "playbackAccessToken": {"signature": "s", "value": "{}"}, "videoQualities": [{"quality": "720", "sourceURL": "https://clips.example.cloudfront.net/c.mp4"}]}}
        elif "user(login" in q:
            who = v["login"]
            data = {"user": {"login": who, "displayName": who.title(), "description": "Practising runs every night.", "profileImageURL": img(who, "avatar"),
                             "stream": stream(who),
                             "videos": {"edges": [{"node": {"id": "1234567", "title": "Last night's runs", "lengthSeconds": 7200, "publishedAt": "2026-11-07T00:00:00Z",
                                                             "viewCount": 900, "previewThumbnailURL": img(who, "vod")}}]},
                             "clips": {"edges": [{"node": {"slug": "BigMoment-abc", "title": "Big moment", "durationSeconds": 3, "viewCount": 50,
                                                            "createdAt": "2026-11-07T00:00:00Z", "thumbnailURL": img(who, "clip")}}]}}}
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
live.requests = types.SimpleNamespace(  # live.py's calls only: the made-up https addresses, answered by the stand-in
    get=lambda url, **kw: requests.get(UP + urlparse(url).path + (f"?{urlparse(url).query}" if urlparse(url).query else ""), **kw),
    post=lambda url, **kw: requests.post(UP + "/gql", **kw), RequestException=requests.RequestException)
yt.video_info = lambda vid, uid=None, fresh=False: {
    "id": vid, "title": "Rainy night radio", "channel": "Night Radio", "channel_id": "UC" + "x" * 22, "live_status": "is_live", "description": "",
    "formats": [{"manifest_url": "https://manifest.example.googlevideo.com/yt/live.m3u8", "protocol": "m3u8_native"}], "thumbnail": None}


async def irc(ws):  # Twitch's chat, standing in: one welcome, then a line every half second
    await ws.recv()
    n = 0
    try:
        while True:
            n += 1
            name, colour, text = (x.format(n=n) for x in CHAT[(n - 1) % len(CHAT)])
            await ws.send(f"@color={colour};display-name={name} :{name.lower()}!v@v.tmi.twitch.tv PRIVMSG #speedy :{text}")
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
