"""Live smoke test against a running yeerr (real YouTube and Spotify):
    python3 tests/smoke.py [http://127.0.0.1:8800] [name] [password]    (or set YEERR_URL)
Signs in (or does first-run setup), searches, opens a video, streams a range,
downloads a short video and its audio, and checks the music section."""
import http.cookiejar
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("YEERR_URL", "http://127.0.0.1:8800")).rstrip("/")
NAME = sys.argv[2] if len(sys.argv) > 2 else "smoke"
PASSWORD = sys.argv[3] if len(sys.argv) > 3 else "smoke-test-password"
ZOO = "jNQXAC9IVRw"  # "Me at the zoo": 19 seconds
LOFI = "UCSJ4gkVC6NrvII8umztf0Ow"
jar = http.cookiejar.CookieJar()
web = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def call(method, path, body=None, headers=None, raw=False):
    req = urllib.request.Request(BASE + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-Requested-With": "parlor", "content-type": "application/json", **(headers or {})})
    try:
        with web.open(req, timeout=120) as r:
            data = r.read()
            return r.status, (data if raw else json.loads(data or b"null")), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null"), dict(e.headers)


def step(name, fn):
    started = time.time()
    try:
        detail = fn()
        print(f"✓ {name} ({time.time() - started:.1f}s){': ' + detail if detail else ''}")
        return True
    except Exception as exc:  # report and keep going
        print(f"✗ {name} ({time.time() - started:.1f}s): {type(exc).__name__}: {exc}")
        return False


def ok(cond, msg):
    if not cond:
        raise AssertionError(msg)


def sign_in():
    s, state, _ = call("GET", "/api/auth/state")
    path = "/api/auth/setup" if state["setup"] else "/api/auth/login"
    s, body, _ = call("POST", path, {"name": NAME, "password": PASSWORD})
    ok(s == 200, f"{path}: {s} {body}")
    return f"{'set up' if state['setup'] else 'signed in'} as {NAME}"


def search():
    s, r, _ = call("GET", "/api/yt/search?q=big+buck+bunny")
    ok(s == 200 and r["items"], f"{s} {r}")
    return f"{len(r['items'])} results, first: {r['items'][0].get('title')}"


video = {}


def open_video():
    s, v, _ = call("GET", f"/api/yt/video/{ZOO}")
    ok(s == 200, f"{s} {v}")
    ok(v["video"] and v["audio"], "no streams")
    video.update(v)
    return f"{v['title']!r}, {len(v['video'])} video + {len(v['audio'])} audio streams, best {v['video'][0]['height']}p"


def stream():
    fmt = video["video"][-1]["id"]
    s, data, h = call("GET", f"/api/yt/stream/{ZOO}/{fmt}", headers={"Range": "bytes=0-65535"}, raw=True)
    ok(s == 206 and len(data) == 65536, f"{s} {len(data)}")
    a = video["audio"][0]["id"]
    s2, data2, h2 = call("GET", f"/api/yt/stream/{ZOO}/{a}", headers={"Range": "bytes=1000-1999"}, raw=True)
    ok(s2 == 206 and len(data2) == 1000, f"audio {s2} {len(data2)}")
    return f"video {h.get('content-range')}, audio {h2.get('content-range')}"


def extras():
    s, segs, _ = call("GET", f"/api/yt/segments/{ZOO}")
    ok(s == 200 and isinstance(segs, list), f"segments {s}")
    s, ch, _ = call("GET", f"/api/yt/channel/{LOFI}")
    ok(s == 200 and ch["items"], f"channel {s}")
    return f"{len(segs)} SponsorBlock segments; channel {ch['name']!r} with {len(ch['items'])} videos"


def feed():
    call("POST", "/api/me/subs", {"channel_id": LOFI, "name": "Lofi Girl"})
    s, r, _ = call("GET", "/api/yt/feed")
    ok(s == 200 and r["items"], f"{s} {r}")
    return f"{len(r['items'])} videos from {r['channels']} channel(s)"


def browse():
    s, rel, _ = call("GET", f"/api/yt/related/{ZOO}")
    ok(s == 200 and len(rel["items"]) >= 5 and all(i["title"] and i["channel_id"] for i in rel["items"]), f"related {s} {rel}")
    s, top, _ = call("GET", "/api/yt/topic?q=science")
    ok(s == 200 and top["items"], f"topic {s} {top}")
    s, mine, _ = call("GET", "/api/me/topics")
    ok(s == 200 and mine["topics"], f"topics {s}")
    return f"{len(rel['items'])} related videos; {len(top['items'])} in this week's science shelf"


def download(kind, quality):
    def run():
        s, job, _ = call("POST", "/api/yt/downloads", {"id": ZOO, "quality": quality, "kind": kind})
        ok(s == 200, f"{s} {job}")
        for _ in range(240):
            time.sleep(1)
            jobs = call("GET", "/api/yt/downloads")[1]
            j = next(j for j in jobs if j["job"] == job["job"])
            if j["status"] in ("done", "failed", "stopped"):
                break
        ok(j["status"] == "done", f"{j['status']}: {j.get('error')}")
        return f"{j['path']} ({j['size']} bytes)"
    return run


def play_download():
    s, lib, _ = call("GET", "/api/yt/library")
    ok(any(i["id"] == ZOO for i in lib["items"]), "not in the library")
    s, data, h = call("GET", f"/api/yt/file/{ZOO}", headers={"Range": "bytes=0-99"}, raw=True)
    ok(s == 206 and len(data) == 100, f"{s} {len(data)}")
    return f"library has it; {h.get('content-range')}"


def music():
    s, r, _ = call("GET", "/music/api/search?q=radiohead")
    ok(s == 200 and r.get("artists"), f"search {s} {str(r)[:200]}")
    s, lib, _ = call("GET", "/music/api/library")
    ok(s == 200, f"library {s}")
    return f"Spotify search ok; {len(lib['songs'])} songs in the music folder"


def seerr():
    s, st, _ = call("GET", "/api/seerr/status")
    if not st.get("connected"):
        return f"skipped: Seerr not connected ({st.get('reason')})"
    s, d, _ = call("GET", "/api/seerr/discover")
    ok(s == 200 and d["trending"] and d["movies"], f"discover {s}")
    s, r, _ = call("GET", "/api/seerr/search?q=the%20matrix")
    ok(s == 200 and any(x["type"] == "movie" for x in r["items"]), f"search {s}")
    s, m, _ = call("GET", "/api/seerr/movie/603")
    ok(s == 200 and m["title"] == "The Matrix" and m["cast"], f"movie {s} {str(m)[:200]}")
    s, tv, _ = call("GET", "/api/seerr/tv/1399")
    ok(s == 200 and tv["seasons"], f"tv {s}")
    s, p, _ = call("GET", "/api/seerr/person/6384")
    ok(s == 200 and p["items"], f"person {s}")
    s, req, _ = call("POST", "/api/seerr/request", {"type": "movie", "id": 603})
    ok(s == 200 or (s == 409 and "already" in str(req).lower()), f"request {s} {req}")
    s, mine, _ = call("GET", "/api/seerr/requests?scope=all")
    ok(s == 200, f"requests {s} {mine}")
    return (f"Seerr {st.get('version')}: {len(d['trending'])} trending; The Matrix has {len(m['cast'])} cast, "
            f"trailer {bool(m['trailer'])}; Game of Thrones {len(tv['seasons'])} seasons; request ok; {mine.get('total')} request(s)")


checks = [("health", lambda: ok(call("GET", "/api/health")[0] == 200, "down")), ("sign in", sign_in), ("YouTube search", search),
          ("open a video", open_video), ("stream ranges", stream), ("SponsorBlock and a channel", extras), ("subscriptions feed", feed),
          ("browse: related videos and topics", browse),
          ("download a video", download("video", "480")), ("play the download", play_download),
          ("save audio to music", download("music", "best")), ("music section", music), ("movies and TV (Seerr)", seerr)]
results = [step(name, fn) for name, fn in checks]
print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
