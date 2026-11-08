"""The Library in a real browser: Parlor runs here against a Jellyfin stand-in (a small HTTP server speaking the
Jellyfin routes Parlor uses) that serves real media made with ffmpeg: a film, two episodes, an album and an EPUB.
A member plays the film with seeking and its place is saved (Continue watching then shows it), opens the show,
its season and plays an episode, plays the album in the dock and keeps it playing across pages, reads the book and
turns a page, and plays a song from the music folder; a member without Library is refused. No page errors;
screenshots at desktop and phone width:
   python tests/ui_library.py [OUT_DIR]   (needs Playwright, its Chromium and ffmpeg)"""
import json
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import auth
import core
import uvicorn
from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="ui-library-"))
OUT.mkdir(parents=True, exist_ok=True)
MEDIA = Path(tempfile.mkdtemp(prefix="jf-media-"))
PASSWORD, KEY = "Ui-library-pass-42!", "STANDINKEY0123"  # pragma: allowlist secret (made up)
T = 10_000_000


def ff(*args):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=True)


def video(name, seconds, colour):
    ff("-f", "lavfi", "-i", f"testsrc2=duration={seconds}:size=426x240:rate=24", "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}",
       "-vf", f"drawbox=c={colour}@0.35:t=fill", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", "-shortest", str(MEDIA / name))


def picture(name, size, colour):
    ff("-f", "lavfi", "-i", f"gradients=s={size}:c0={colour}:c1=0xf2c14e:c2=0x2a1f4a:n=3:duration=1:speed=0", "-frames:v", "1", str(MEDIA / name))


video("film.mp4", 12, "0x6cc2ff")
video("ep1.mp4", 6, "0xa58bff")
video("ep2.mp4", 6, "0xff6b6b")
for n, hz in (("song1.mp3", 440), ("song2.mp3", 523)):
    ff("-f", "lavfi", "-i", f"sine=frequency={hz}:duration=20", "-c:a", "libmp3lame", "-b:a", "96k", str(MEDIA / n))
picture("poster.png", "400x600", "0x2f6fa8")
picture("wide.png", "960x540", "0x5a3fa0")
picture("square.png", "500x500", "0xd08a2a")
with zipfile.ZipFile(MEDIA / "book.epub", "w") as z:
    z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
    z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
               '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
    chapters = [(f"c{i}", f"Chapter {i}", "".join(f"<p>{'The lighthouse keeper counted the ships again. ' * 9}Part {i}.{k}</p>" for k in range(40)))
                for i in (1, 2)]
    z.writestr("OEBPS/content.opf", '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
               '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">parlor-test-book</dc:identifier><dc:title>The Keeper</dc:title>'
               '<dc:language>en</dc:language></metadata><manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
               + "".join(f'<item id="{c}" href="{c}.xhtml" media-type="application/xhtml+xml"/>' for c, _, _ in chapters)
               + '</manifest><spine>' + "".join(f'<itemref idref="{c}"/>' for c, _, _ in chapters) + "</spine></package>")
    z.writestr("OEBPS/nav.xhtml", '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body>'
               '<nav epub:type="toc"><ol>' + "".join(f'<li><a href="{c}.xhtml">{t}</a></li>' for c, t, _ in chapters) + "</ol></nav></body></html>")
    for c, title, body in chapters:
        z.writestr(f"OEBPS/{c}.xhtml", f'<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>{title}</title></head>'
                                       f"<body><h1>{title}</h1>{body}</body></html>")


def hid(n):
    return f"{n:032x}"


UID = hid(0xF)
V_FILMS, V_SHOWS, V_MUSIC, V_BOOKS = hid(0x100), hid(0x101), hid(0x102), hid(0x103)
FILM, SERIES, SEASON, EP1, EP2, ALBUM, S1, S2, ARTIST, BOOK = (hid(n) for n in range(0x200, 0x20A))
MS = hid(0x999)
art = {"ImageTags": {"Primary": "p"}, "BackdropImageTags": ["b"]}
ITEMS = {
    FILM: {"Id": FILM, "Name": "Night Train", "Type": "Movie", "ProductionYear": 2024, "RunTimeTicks": 12 * T, "Overview": "A sleeper car, a missing ticket.",
           "Genres": ["<span><img src=x onerror=window.__pwned=1>", "Mystery"], "CommunityRating": 7.6, "OfficialRating": "PG", "People": [{"Name": "Ada Brook", "Role": "Conductor"}], "_file": "film.mp4",
           "_view": V_FILMS, **art},
    SERIES: {"Id": SERIES, "Name": "Harbour Lights", "Type": "Series", "ProductionYear": 2023, "Overview": "A small port, a long winter.", "_view": V_SHOWS, **art},
    SEASON: {"Id": SEASON, "Name": "Season 1", "Type": "Season", "SeriesId": SERIES, "SeriesName": "Harbour Lights", "IndexNumber": 1, **art},
    EP1: {"Id": EP1, "Name": "Low Tide", "Type": "Episode", "SeriesId": SERIES, "SeriesName": "Harbour Lights", "SeasonId": SEASON, "ParentIndexNumber": 1,
          "IndexNumber": 1, "RunTimeTicks": 6 * T, "Overview": "The ferry is late.", "_file": "ep1.mp4", **art},
    EP2: {"Id": EP2, "Name": "Fog Bell", "Type": "Episode", "SeriesId": SERIES, "SeriesName": "Harbour Lights", "SeasonId": SEASON, "ParentIndexNumber": 1,
          "IndexNumber": 2, "RunTimeTicks": 6 * T, "Overview": "Nobody rings it.", "_file": "ep2.mp4", **art},
    ALBUM: {"Id": ALBUM, "Name": "Quiet Hours", "Type": "MusicAlbum", "AlbumArtist": "The Lamps", "Artists": ["The Lamps"], "ProductionYear": 2022,
            "_view": V_MUSIC, "ImageTags": {"Primary": "p"}},
    S1: {"Id": S1, "Name": "First Light", "Type": "Audio", "Artists": ["The Lamps"], "Album": "Quiet Hours", "AlbumId": ALBUM, "IndexNumber": 1,
         "RunTimeTicks": 20 * T, "AlbumPrimaryImageTag": "p", "_file": "song1.mp3", "MediaType": "Audio"},
    S2: {"Id": S2, "Name": "Second Wind", "Type": "Audio", "Artists": ["The Lamps"], "Album": "Quiet Hours", "AlbumId": ALBUM, "IndexNumber": 2,
         "RunTimeTicks": 20 * T, "AlbumPrimaryImageTag": "p", "_file": "song2.mp3", "MediaType": "Audio"},
    ARTIST: {"Id": ARTIST, "Name": "The Lamps", "Type": "MusicArtist", "ImageTags": {"Primary": "p"}},
    BOOK: {"Id": BOOK, "Name": "The Keeper", "Type": "Book", "ProductionYear": 2021, "Overview": "A lighthouse, a ledger.", "_view": V_BOOKS,
           "ImageTags": {"Primary": "p"}, "_file": "book.epub"},
}
VIEWS = [{"Id": V_FILMS, "Name": "Films", "Type": "CollectionFolder", "CollectionType": "movies", **art},
         {"Id": V_SHOWS, "Name": "Shows", "Type": "CollectionFolder", "CollectionType": "tvshows", **art},
         {"Id": V_MUSIC, "Name": "Music", "Type": "CollectionFolder", "CollectionType": "music", "ImageTags": {"Primary": "p"}},
         {"Id": V_BOOKS, "Name": "Books", "Type": "CollectionFolder", "CollectionType": "books", "ImageTags": {"Primary": "p"}}]
STATE: dict = {}  # item id -> UserData
SAVES: list = []


def public(i):
    out = {k: v for k, v in i.items() if not k.startswith("_")}
    out["UserData"] = {"Played": False, "PlaybackPositionTicks": 0, "IsFavorite": False, **STATE.get(i["Id"], {})}
    return out


class StandIn(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, status, data=None, body=None, ctype="application/json", headers=()):
        body = json.dumps(data).encode() if body is None else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def file(self, path, ctype):
        data = path.read_bytes()
        rng = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
        if not rng:
            return self.send(200, body=data, ctype=ctype, headers=[("Accept-Ranges", "bytes")])
        start = int(rng[1] or 0)
        end = min(int(rng[2]) if rng[2] else len(data) - 1, len(data) - 1)
        return self.send(206, body=data[start:end + 1], ctype=ctype, headers=[("Accept-Ranges", "bytes"), ("Content-Range", f"bytes {start}-{end}/{len(data)}")])

    def listing(self, items):
        return self.send(200, {"Items": [public(i) for i in items], "TotalRecordCount": len(items)})

    def do_GET(self):  # noqa: C901  (one router for a stand-in)
        if f'Token="{KEY}"' not in (self.headers.get("Authorization") or ""):
            return self.send(401, {})
        url = urlsplit(self.path)
        q = {k.lower(): v[0] for k, v in parse_qs(url.query).items()}
        p = url.path
        if p == "/System/Info":
            return self.send(200, {"ServerName": "Stand-in", "Version": "10.10.7"})
        if p == "/Users":
            return self.send(200, [{"Id": UID, "Name": "family", "Policy": {"EnableContentDownloading": True}}])
        if p == "/UserViews":
            return self.send(200, {"Items": VIEWS})
        if p == "/Items/Latest":
            return self.send(200, [public(i) for i in ITEMS.values() if i.get("_view") == q.get("parentid")])
        if p == "/UserItems/Resume":
            return self.listing([i for i in ITEMS.values() if STATE.get(i["Id"], {}).get("PlaybackPositionTicks") and not STATE[i["Id"]].get("Played")])
        if p == "/Shows/NextUp":
            return self.listing([i for i in (ITEMS[EP1], ITEMS[EP2]) if not STATE.get(i["Id"], {}).get("Played")][:1])
        if p == f"/Shows/{SERIES}/Seasons":
            return self.listing([ITEMS[SEASON]])
        if p == f"/Shows/{SERIES}/Episodes":
            return self.listing([ITEMS[EP1], ITEMS[EP2]])
        if p == "/Items":
            items = list(ITEMS.values())
            if q.get("parentid") == ALBUM:
                items = [ITEMS[S1], ITEMS[S2]]
            elif q.get("artistids"):
                items = [ITEMS[ALBUM]]
            elif q.get("parentid"):
                items = [i for i in items if i.get("_view") == q["parentid"]]
            if q.get("includeitemtypes"):
                items = [i for i in items if i["Type"] in q["includeitemtypes"].split(",")]
            if q.get("searchterm"):
                items = [i for i in items if q["searchterm"].lower() in i["Name"].lower()]
            return self.listing(items)
        m = re.fullmatch(r"/Items/(\w+)(?:/(Similar|Download|Images/\w+(?:/\d+)?))?", p)
        i = ITEMS.get(m and m[1]) or next((v for v in VIEWS if m and v["Id"] == m[1]), None)
        if i:
            if not m[2]:
                return self.send(200, public(i))
            if m[2] == "Similar":
                return self.listing([])
            if m[2] == "Download":
                return self.file(MEDIA / i["_file"], "application/epub+zip")
            kind = "wide.png" if "Backdrop" in m[2] or i["Type"] == "Episode" else "square.png" if i["Type"] in ("MusicAlbum", "Audio", "MusicArtist") else "poster.png"
            return self.file(MEDIA / kind, "image/png")
        m = re.fullmatch(r"/(Videos|Audio)/(\w+)/stream(?:\.\w+)?", p)
        if m and m[2] in ITEMS:
            return self.file(MEDIA / ITEMS[m[2]]["_file"], "video/mp4" if m[1] == "Videos" else "audio/mpeg")
        return self.send(404, {})

    do_HEAD = do_GET

    def do_POST(self):
        if f'Token="{KEY}"' not in (self.headers.get("Authorization") or ""):
            return self.send(401, {})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        p = urlsplit(self.path).path
        m = re.fullmatch(r"/Items/(\w+)/PlaybackInfo", p)
        if m and m[1] in ITEMS:
            i = ITEMS[m[1]]
            audio = i["Type"] == "Audio"
            streams = [] if audio else [{"Index": 0, "Type": "Video", "Codec": "h264"}, {"Index": 1, "Type": "Audio", "Codec": "aac", "Language": "eng",
                                                                                         "DisplayTitle": "English AAC", "IsDefault": True}]
            return self.send(200, {"PlaySessionId": "ps", "MediaSources": [{"Id": MS, "Container": "mp3" if audio else "mp4", "SupportsDirectPlay": True,
                                                                             "RunTimeTicks": i["RunTimeTicks"], "MediaStreams": streams}]})
        m = re.fullmatch(r"/UserItems/(\w+)/UserData", p)
        if m and m[1] in ITEMS:
            STATE.setdefault(m[1], {}).update(body)
            SAVES.append((m[1], body))
            return self.send(200, STATE[m[1]])
        return self.send(404, {})


def port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


JF = ThreadingHTTPServer(("127.0.0.1", port()), StandIn)
threading.Thread(target=JF.serve_forever, daemon=True).start()
core.db()
ids = {n: auth.create_user(n, PASSWORD, n == "uiadmin") for n in ("uiadmin", "viewer", "closed")}
core.db().execute("UPDATE users SET blocked = 'library' WHERE id = ?", (ids["closed"],))
for k, v in (("jellyfin_url", f"http://127.0.0.1:{JF.server_address[1]}"), ("jellyfin_key", KEY), ("jellyfin_user", "family")):
    core.set_setting(k, v)
(Path.cwd() / "Lamps" / "Shore").mkdir(parents=True, exist_ok=True)  # a song in the music folder, tagged
ff("-f", "lavfi", "-i", "sine=frequency=392:duration=15", "-metadata", "title=Tidewater", "-metadata", "artist=The Lamps", "-metadata", "album_artist=The Lamps",
   "-metadata", "album=Shore", "-metadata", "track=1", "-c:a", "libmp3lame", "-b:a", "96k", str(Path.cwd() / "Lamps" / "Shore" / "01 - Tidewater.mp3"))

PORT = port()
server = uvicorn.Server(uvicorn.Config(env.yeerr.app, host="127.0.0.1", port=PORT, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.1)
BASE = f"http://127.0.0.1:{PORT}"
SHELL = next(iter(sorted(Path.home().glob(".cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"))), None)
errors, checks, missing = [], {}, []


def session(b, name, size):
    ctx = b.new_context(viewport=size, color_scheme="dark")
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(f"{name}: {e}"))
    page.goto(BASE + "/")
    page.wait_for_selector("#gateForm")
    page.fill("#gName", name)
    page.fill("#gPw", PASSWORD)
    page.click("#gateForm .primary")
    page.wait_for_selector("#gateForm", state="detached", timeout=30000)
    return ctx, page


def settle(page, ms=700):
    page.wait_for_timeout(ms)


def plays(page, sel, past):
    page.wait_for_function(f"document.querySelector('{sel}') && document.querySelector('{sel}').readyState >= 1", timeout=30000)
    page.evaluate(f"(() => {{ const v = document.querySelector('{sel}'); v.muted = true; return v.play(); }})()")
    page.wait_for_function(f"document.querySelector('{sel}').currentTime > {past}", timeout=30000)
    return True


def report_failure(page):
    """On a timeout: what the page showed, for the log."""
    try:
        page.screenshot(path=str(OUT / "failed.png"))
        print("FAILED at", page.url, "\n", page.inner_text("main")[:600], "\npage errors:", errors)
    except Exception:  # noqa: BLE001  (the report is best effort)
        pass


with sync_playwright() as p:
    b = p.chromium.launch(executable_path=str(SHELL) if SHELL else None, args=["--autoplay-policy=no-user-gesture-required"])
    vctx, page = session(b, "viewer", {"width": 1366, "height": 900})
    try:
        page.on("response", lambda r: r.status >= 400 and missing.append(f"{r.status} {r.url.replace(BASE, '')}"))
        page.goto(BASE + "/#/jf")
        page.wait_for_selector(".jcard", timeout=30000)
        checks["the home shows each library's newest"] = all(page.locator(f".shelf-head h2:has-text('New in {n}')").count() for n in ("Films", "Shows", "Music", "Books"))
        checks["the nav has Library"] = page.locator("#side [data-g=library]").count() == 1
        settle(page)
        page.screenshot(path=str(OUT / "home-desk.png"))

        # a film: play, seek, and the place is saved
        page.goto(BASE + "/#/jf/movies")
        page.wait_for_selector(".jgrid .jcard", timeout=30000)
        page.click(".jcard a.t:has-text('Night Train')")
        page.wait_for_selector(".hero .primary", timeout=30000)
        settle(page)
        checks["metadata from Jellyfin is shown as text, never run"] = page.evaluate("window.__pwned === undefined") and "<img src=x" in page.inner_text(".hero .meta")
        page.screenshot(path=str(OUT / "film-desk.png"))
        page.click(".hero .primary")
        page.wait_for_selector("#jfVideo", timeout=30000)
        plays(page, "#jfVideo", 0.3)
        page.evaluate("document.querySelector('#jfVideo').currentTime = 7")
        page.wait_for_function("document.querySelector('#jfVideo').currentTime > 7.3", timeout=30000)
        page.evaluate("document.querySelector('#jfVideo').pause()")
        page.wait_for_timeout(800)
        saved = [b for i, b in SAVES if i == FILM and b.get("PlaybackPositionTicks", 0) >= 7 * T]
        checks["the film plays, seeks, and its place is saved in Jellyfin"] = bool(saved)
        page.screenshot(path=str(OUT / "player-desk.png"))
        page.goto(BASE + "/#/jf")
        page.wait_for_selector(".shelf-head h2:has-text('Continue watching')", timeout=30000)
        checks["Continue watching shows it"] = page.locator(".jstrip.wide .jcard:has-text('Night Train') .meter").count() > 0

        # a show: its season, then an episode
        page.goto(BASE + f"/#/jf/item/{SERIES}")
        page.wait_for_selector(".jstrip .jcard a.t:has-text('Season 1')", timeout=30000)
        page.click(".jstrip .jcard a.t:has-text('Season 1')")
        page.wait_for_selector(".eps li", timeout=30000)
        checks["the show lists its season and episodes"] = page.locator(".eps li").count() == 2
        settle(page)
        page.screenshot(path=str(OUT / "season-desk.png"))
        page.click(".eps li:nth-child(2) a")
        page.wait_for_selector("#jfVideo", timeout=30000)
        checks["an episode plays"] = plays(page, "#jfVideo", 0.5) and page.locator(".eps li.now").count() == 1

        # an album in the dock, still playing on another page
        page.goto(BASE + f"/#/jf/item/{ALBUM}")
        page.wait_for_selector("[data-album-play]", timeout=30000)
        page.click("[data-album-play]")
        page.wait_for_function("!dock.audio.paused && dock.audio.currentTime > 0.5", timeout=30000)
        page.wait_for_selector(".jtracks .row.now", timeout=10000)
        settle(page, 1200)
        page.screenshot(path=str(OUT / "album-desk.png"))
        t0 = page.evaluate("dock.audio.currentTime")
        page.goto(BASE + "/#/jf/books")
        page.wait_for_selector(".jgrid .jcard", timeout=30000)
        page.wait_for_function(f"dock.audio.currentTime > {t0 + 1}", timeout=30000)
        checks["the song keeps playing in the dock across pages"] = page.evaluate("!dock.audio.paused") and page.is_visible("#dock")
        page.click("#dockNext")
        page.wait_for_function("dock.at === 1 && !dock.audio.paused && dock.audio.currentTime > 0.3", timeout=30000)
        checks["the dock moves to the next song"] = page.inner_text("#dockTitle") == "Second Wind"

        # the book: open, read, turn a page
        page.goto(BASE + f"/#/jf/read/{BOOK}")
        page.wait_for_selector("#rPage iframe", timeout=30000)
        page.wait_for_function("document.querySelector('#rPage iframe').contentDocument?.body?.innerText.includes('lighthouse keeper')", timeout=30000)
        page.wait_for_function("document.querySelector('#rAt').textContent.endsWith('%')", timeout=30000)
        before = page.evaluate("JSON.parse(localStorage.getItem('parlor:book:" + BOOK + "'))")
        page.click("#rNext")
        page.wait_for_function("JSON.parse(localStorage.getItem('parlor:book:" + BOOK + "')) !== " + json.dumps(before), timeout=30000)
        checks["the book opens and turns a page"] = True
        settle(page)
        page.screenshot(path=str(OUT / "reader-desk.png"))
        page.click(".rbar a.ghost")

        # a song from the music folder plays in the dock
        page.goto(BASE + "/#/library")
        page.wait_for_selector(".tile a.name:has-text('Shore')", timeout=60000)
        page.click(".tile a.name:has-text('Shore')")
        page.wait_for_selector("[data-local-play]", timeout=30000)
        page.click(".hero [data-local-play]")
        page.wait_for_function("!dock.audio.paused && dock.audio.currentTime > 0.5 && dock.audio.src.includes('/api/jf/music/')", timeout=30000)
        checks["a song in the music folder plays in the dock"] = page.inner_text("#dockTitle") == "Tidewater"

        # phone width
        pctx = b.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark", is_mobile=True, has_touch=True)
        phone = pctx.new_page()
        phone.on("pageerror", lambda e: errors.append(f"phone: {e}"))
        pctx.add_cookies(vctx.cookies())
        for name, hash_, sel in (("home-phone", "#/jf", ".jcard"), ("film-phone", f"#/jf/item/{FILM}", ".hero .primary"), ("album-phone", f"#/jf/item/{ALBUM}", "[data-album-play]")):
            phone.goto(BASE + "/" + hash_)
            phone.wait_for_selector(sel, timeout=30000)
            if name == "album-phone":
                phone.click("[data-album-play]")
                phone.wait_for_function("!dock.audio.paused", timeout=30000)
            settle(phone, 900)
            phone.screenshot(path=str(OUT / f"{name}.png"))
        checks["no sideways scroll on a phone"] = phone.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")

        # a member without Library
        cctx, closed = session(b, "closed", {"width": 1366, "height": 900})
        closed.goto(BASE + "/#/jf")
        closed.wait_for_selector("main .home, main .note", timeout=30000)
        status = closed.evaluate("fetch('/api/jf/views').then((r) => r.status)")
        checks["a member without Library is refused"] = status == 403 and closed.locator("#side [data-g=library]").count() == 0 and "isn't open to your account" in closed.inner_text("main")
    except Exception:
        report_failure(page)
        raise
    b.close()
server.should_exit = True
JF.shutdown()
for k, v in checks.items():
    print(f"  {'✓' if v else '✗'} {k}")
print("page errors:", errors or "none")
print("failed requests:", missing or "none")
shots = sorted(x.name for x in OUT.glob("*.png"))
print("screenshots:", ", ".join(shots))
ok = len(checks) == 13 and all(checks.values()) and not errors and len(shots) == 9
print("RESULT:", "ok" if ok else "FAIL")
sys.exit(0 if ok else 1)
