"""The Library against a real Jellyfin: starts jellyfin/jellyfin in Docker with made-up media (a film it can send as it
is, one it has to convert, an episode, an album and an EPUB), runs Jellyfin's first-run setup, makes an API key,
points an in-process Parlor at it and plays each in a real browser: direct play, Jellyfin's conversion over HLS
through Parlor's proxy (with seeking), the place saved in Jellyfin, the album in the dock and the book in the reader.
The container and its media are removed afterwards.
   python tests/live_library.py [OUT_DIR]   (needs Docker, ffmpeg, Playwright and the jellyfin/jellyfin image)"""
import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import requests

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import auth
import core
import uvicorn
from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="live-library-"))
OUT.mkdir(parents=True, exist_ok=True)
MEDIA = Path(tempfile.mkdtemp(prefix="jf-live-media-"))
NAME, PASSWORD = "liveadmin", "Live-library-pass-42!"  # pragma: allowlist secret (made up)
CONTAINER = "parlor-live-library-jellyfin"
HEAD = 'MediaBrowser Client="live", Device="live", DeviceId="parlor-live", Version="1"'


def ff(*args):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=True)


def port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_media():
    for d in ("films/Night Train (2024)", "films/Fog Line (2023)", "shows/Harbour Lights/Season 01", "music/The Lamps/Quiet Hours", "books"):
        (MEDIA / d).mkdir(parents=True, exist_ok=True)
    src = ["-f", "lavfi", "-i", "testsrc2=duration=40:size=640x360:rate=24", "-f", "lavfi", "-i", "sine=frequency=330:duration=40"]
    ff(*src, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", "-shortest", str(MEDIA / "films/Night Train (2024)/Night Train (2024).mp4"))
    # an old AVI no browser plays as it is: Jellyfin has to convert it
    ff(*src, "-c:v", "mpeg4", "-q:v", "5", "-c:a", "libmp3lame", "-shortest", str(MEDIA / "films/Fog Line (2023)/Fog Line (2023).avi"))
    ff(*src[:4], "-t", "20", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(MEDIA / "shows/Harbour Lights/Season 01/Harbour Lights - S01E01 - Low Tide.mp4"))
    for n, (title, hz) in enumerate((("First Light", 440), ("Second Wind", 523)), 1):
        ff("-f", "lavfi", "-i", f"sine=frequency={hz}:duration=25", "-metadata", f"title={title}", "-metadata", "artist=The Lamps", "-metadata", "album_artist=The Lamps",
           "-metadata", "album=Quiet Hours", "-metadata", f"track={n}", "-c:a", "libmp3lame", "-b:a", "96k", str(MEDIA / f"music/The Lamps/Quiet Hours/0{n} - {title}.mp3"))
    with zipfile.ZipFile(MEDIA / "books/The Keeper.epub", "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                   '<rootfiles><rootfile full-path="content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("content.opf", '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
                   '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">parlor-live-book</dc:identifier><dc:title>The Keeper</dc:title>'
                   '<dc:creator>A. Writer</dc:creator><dc:language>en</dc:language></metadata><manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
                   '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="c1"/></spine></package>')
        z.writestr("nav.xhtml", '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body>'
                   '<nav epub:type="toc"><ol><li><a href="c1.xhtml">One</a></li></ol></nav></body></html>')
        z.writestr("c1.xhtml", '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>One</title></head><body><h1>One</h1>'
                   + "<p>The lighthouse keeper counted the ships again.</p>" * 200 + "</body></html>")
    MEDIA.chmod(0o755)
    subprocess.run(["chmod", "-R", "a+rX", str(MEDIA)], check=True)


JF_PORT = port()
JF = f"http://127.0.0.1:{JF_PORT}"


def jf(method, path, token=None, **kw):
    auth_header = HEAD + (f', Token="{token}"' if token else "")
    return requests.request(method, JF + path, headers={"Authorization": auth_header}, timeout=60, **kw)


def wait(what, check, seconds):
    end = time.time() + seconds
    while time.time() < end:
        try:
            if check():
                return
        except requests.RequestException:
            pass
        time.sleep(2)
    raise SystemExit(f"timed out waiting for {what}")


def set_up_jellyfin():
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--name", CONTAINER, "-p", f"127.0.0.1:{JF_PORT}:8096", "-v", f"{MEDIA}:/media:ro", "jellyfin/jellyfin:latest"],
                   check=True, capture_output=True)
    wait("Jellyfin to start", lambda: jf("GET", "/System/Info/Public").ok, 180)
    wait("the first-run setup", lambda: jf("POST", "/Startup/Configuration", json={"UICulture": "en-US", "MetadataCountryCode": "US",
                                                                                     "PreferredMetadataLanguage": "en"}).ok, 60)
    jf("GET", "/Startup/User")
    assert jf("POST", "/Startup/User", json={"Name": NAME, "Password": PASSWORD}).ok
    jf("POST", "/Startup/RemoteAccess", json={"EnableRemoteAccess": True, "EnableAutomaticPortMapping": False})
    assert jf("POST", "/Startup/Complete").ok
    token = jf("POST", "/Users/AuthenticateByName", json={"Username": NAME, "Pw": PASSWORD}).json()["AccessToken"]
    for name, kind in (("Films", "movies"), ("Shows", "tvshows"), ("Music", "music"), ("Books", "books")):
        r = jf("POST", "/Library/VirtualFolders", token, params={"name": name, "collectionType": kind, "refreshLibrary": "false"},
               json={"LibraryOptions": {"PathInfos": [{"Path": f"/media/{name.lower()}"}], "EnableRealtimeMonitor": False, "EnableInternetProviders": False}})
        assert r.ok, (name, r.status_code, r.text[:200])
    jf("POST", "/Library/Refresh", token)
    me = jf("GET", "/Users/Me", token).json()["Id"]

    def scanned():
        r = jf("GET", "/Items", token, params={"userId": me, "recursive": "true", "includeItemTypes": "Movie,Episode,Audio,Book"}).json()
        kinds = [i["Type"] for i in r.get("Items", [])]
        return kinds.count("Movie") == 2 and kinds.count("Audio") == 2 and "Episode" in kinds and "Book" in kinds

    wait("the library scan", scanned, 300)
    assert jf("POST", "/Auth/Keys", token, params={"app": "Parlor"}).ok
    key = next(k["AccessToken"] for k in jf("GET", "/Auth/Keys", token).json()["Items"] if k.get("AppName") == "Parlor")
    items = {i["Name"]: i for i in jf("GET", "/Items", token, params={"userId": me, "recursive": "true",
                                                                    "includeItemTypes": "Movie,Episode,MusicAlbum,Book"}).json()["Items"]}
    return token, me, key, items


errors, checks, missing = [], {}, []
try:
    make_media()
    TOKEN, ME_ID, KEY, ITEMS = set_up_jellyfin()
    core.db()
    auth.create_user("viewer", PASSWORD, False)
    for k, v in (("jellyfin_url", JF), ("jellyfin_key", KEY), ("jellyfin_user", NAME)):
        core.set_setting(k, v)
    PORT = port()
    server = uvicorn.Server(uvicorn.Config(env.yeerr.app, host="127.0.0.1", port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.1)
    BASE = f"http://127.0.0.1:{PORT}"
    SHELL = next(iter(sorted(Path.home().glob(".cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"))), None)
    print("Jellyfin found:", sorted((i["Type"], n) for n, i in ITEMS.items()))
    pick = lambda kind, word="": next(i["Id"] for n, i in ITEMS.items() if i["Type"] == kind and word in n)  # noqa: E731  (Jellyfin may rename from online data)
    film, fog, ep, album, book = pick("Movie", "Night"), pick("Movie", "Fog"), pick("Episode"), pick("MusicAlbum"), pick("Book")

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=str(SHELL) if SHELL else None, args=["--autoplay-policy=no-user-gesture-required"])
        ctx = b.new_context(viewport={"width": 1366, "height": 900}, color_scheme="dark")
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BASE + "/")
        page.wait_for_selector("#gateForm")
        page.fill("#gName", "viewer")
        page.fill("#gPw", PASSWORD)
        page.click("#gateForm .primary")
        page.wait_for_selector("#gateForm", state="detached", timeout=30000)
        page.on("response", lambda r: r.status >= 400 and "/Images/" not in r.url and missing.append(f"{r.status} {r.url}"))  # signed in: nothing should fail now
        try:
            page.goto(BASE + "/#/jf")
            page.wait_for_selector(".shelf-head h2:has-text('New in Films')", timeout=30000)
            page.wait_for_timeout(800)
            page.screenshot(path=str(OUT / "live-home.png"))
            checks["the home lists Jellyfin's libraries"] = page.locator(".jcard a.t:has-text('Night Train')").count() > 0

            def play(item, past, seek=None):
                page.goto(BASE + f"/#/jf/play/{item}")
                page.wait_for_function("document.querySelector('#jfVideo')?.readyState >= 2", timeout=90000)
                page.evaluate("(() => { const v = document.querySelector('#jfVideo'); v.muted = true; return v.play(); })()")
                page.wait_for_function(f"document.querySelector('#jfVideo').currentTime > {past}", timeout=90000)
                if seek:
                    page.evaluate(f"document.querySelector('#jfVideo').currentTime = {seek}")
                    page.wait_for_function(f"document.querySelector('#jfVideo').currentTime > {seek + 1}", timeout=90000)
                return page.inner_text("#jfStat")

            checks["a film Jellyfin can send as it is plays directly"] = play(film, 1) == "Original file"
            stat = play(fog, 1.5, seek=25)
            page.screenshot(path=str(OUT / "live-converted.png"))
            checks["a film Jellyfin converts plays over HLS through Parlor, with seeking"] = stat.startswith("Converted") and page.evaluate("!!jfp.hls || document.querySelector('#jfVideo').canPlayType('application/vnd.apple.mpegurl') !== ''")
            page.evaluate("document.querySelector('#jfVideo').pause()")
            page.wait_for_timeout(1500)
            data = jf("GET", f"/Items/{fog}", TOKEN, params={"userId": ME_ID}).json()["UserData"]
            checks["its place is saved in Jellyfin"] = data.get("PlaybackPositionTicks", 0) >= 25 * 10**7
            checks["an episode plays"] = bool(play(ep, 1))
            page.goto(BASE + f"/#/jf/item/{album}")
            page.wait_for_selector("[data-album-play]", timeout=30000)
            page.click("[data-album-play]")
            page.wait_for_function("!dock.audio.paused && dock.audio.currentTime > 1", timeout=60000)
            checks["the album plays in the dock"] = page.inner_text("#dockTitle") == "First Light"
            page.goto(BASE + f"/#/jf/read/{book}")
            page.wait_for_function("document.querySelector('#rPage iframe')?.contentDocument?.body?.innerText.includes('lighthouse keeper')", timeout=60000)
            page.wait_for_timeout(600)
            page.screenshot(path=str(OUT / "live-reader.png"))
            checks["the book opens in the reader"] = True
            checks["the key never reaches the browser"] = KEY not in page.evaluate("JSON.stringify(performance.getEntriesByType('resource').map((r) => r.name))")
        except Exception:
            page.screenshot(path=str(OUT / "live-failed.png"))
            print("FAILED at", page.url, page.inner_text("main")[:400])
            raise
        finally:
            b.close()
    server.should_exit = True
finally:
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    subprocess.run(["rm", "-rf", str(MEDIA)])
for k, v in checks.items():
    print(f"  {'✓' if v else '✗'} {k}")
print("page errors:", errors or "none")
print("failed requests:", missing or "none")
ok = len(checks) == 8 and all(checks.values()) and not errors and not missing
print("RESULT:", "ok" if ok else "FAIL")
sys.exit(0 if ok else 1)
