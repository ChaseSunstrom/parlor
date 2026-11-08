"""Parlor as a phone app, in a real browser. Installable: a worker, a manifest and its icons. The queue: songs from
the music folder (real audio made with ffmpeg) play in the dock; a song's ⋯ menu plays it next, an album's adds it to the queue; the queue panel reorders by
dragging and by the arrow keys, removes, shuffles and puts the order back, repeats one; the next song is loaded in
the spare player and starts with no gap worth hearing; the lock screen knows what's playing; after a reload the
queue is back, paused where it was. Saved for offline: a Jellyfin album (from a Jellyfin stand-in), a song from
the music folder and a downloaded video, saved in small pieces, one of which breaks off and carries on from where
it stopped, byte for byte. With the network off Parlor opens on its Offline page; the saved songs play in the dock
and the video plays, seeking included; a Jellyfin film and episode save from their Library pages (a film with its
subtitle file; what can't be saved says why) and, offline, the film plays from the device with seeking and its
subtitles, its place reaching Jellyfin once back online; removing one frees its space. Back online the bar goes. On plain http
Settings says how to get https. No page errors; screenshots at desktop and phone width:
   python tests/ui_pwa.py [OUT_DIR]   (needs Playwright, its Chromium and ffmpeg)"""
import hashlib
import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import auth
import core
import jf_standin as jfs
import uvicorn
from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="ui-pwa-"))
OUT.mkdir(parents=True, exist_ok=True)
PASSWORD = "Ui-pwa-pass-42!"  # pragma: allowlist secret (made up)
ALBUMS = {("The Gulls", "Harbor"): [("Dawn", 9, 330), ("Pier", 5, 392), ("Gale", 5, 440), ("Lantern", 6, 494)],
          ("The Gulls", "Shore"): [("Tidewater", 5, 523), ("Undertow", 5, 587)]}


def song(artist, album, n, title, seconds, hz):
    folder = Path.cwd() / artist / album  # the music folder
    folder.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={hz}:duration={seconds}",
                    "-metadata", f"title={title}", "-metadata", f"artist={artist}", "-metadata", f"album_artist={artist}",
                    "-metadata", f"album={album}", "-metadata", f"track={n}", "-c:a", "libmp3lame", "-b:a", "96k",
                    str(folder / f"{n:02d} - {title}.mp3")], check=True)


for (artist, album), tracks in ALBUMS.items():
    for n, (title, seconds, hz) in enumerate(tracks, 1):
        song(artist, album, n, title, seconds, hz)
core.db()
ME = auth.create_user("listener", PASSWORD, True)
jfs.point(core)
VID = "0fFl1nEvId1"  # a downloaded video
rel = f"Chan/An offline one [{VID}].mp4"
(core.VIDEOS / rel).parent.mkdir(parents=True, exist_ok=True)
subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=duration=10:size=426x240:rate=24", "-f", "lavfi", "-i",
                "sine=frequency=660:duration=10", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", "-shortest",
                str(core.VIDEOS / rel)], check=True)
core.db().execute("INSERT INTO videos(id, item, path, size, quality, user_id, added) VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (VID, json.dumps({"id": VID, "title": "An offline one", "channel": "Chan", "duration": 10}), rel,
                   (core.VIDEOS / rel).stat().st_size, "240", ME, time.time()))
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
QUEUE = "[...document.querySelectorAll('#queueList .qrow .what .title')].map(e => e.textContent.trim())"
NOW = "dock.queue[dock.at]?.title"
OPFS = """async () => { let n = 0; for await (const [, h] of (await offDir()).entries()) n += (await h.getFile()).size; return n; }"""
SHA = """async (title) => { const e = offMine().find((x) => x.title === title);
  const f = await (await (await offDir()).getFileHandle(e.file)).getFile();
  return [...new Uint8Array(await crypto.subtle.digest("SHA-256", await f.arrayBuffer()))].map((b) => b.toString(16).padStart(2, "0")).join(""); }"""
SUB = """async () => { const e = offMine().find((x) => x.title === 'Night Train');
  return (await (await (await offDir()).getFileHandle(e.subs[0].file)).getFile()).text(); }"""
AFTER = ["Dawn", "Lantern", "Gale", "Pier", "Undertow", "Lantern"]  # the queue once it has been reordered and Tidewater taken out


def album(page, name):
    page.goto(BASE + f"/#/library/The%20Gulls/{name}")
    page.wait_for_selector(".jtracks [data-local-more]", timeout=30000)


def menu(page, button, choice):
    page.click(button)
    page.wait_for_selector("#queueMenu", timeout=5000)
    page.click(f"#queueMenu [data-qm={choice}]")


def to_end(page, left=1.2):
    """skip to the last moments of the song playing"""
    page.wait_for_function("dock.audio.duration > 0 && !dock.audio.paused", timeout=30000)
    page.evaluate(f"dock.audio.currentTime = dock.audio.duration - {left}")


with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path=str(SHELL) if SHELL else None, args=["--autoplay-policy=no-user-gesture-required"])
    ctx = b.new_context(viewport={"width": 1366, "height": 900}, color_scheme="dark")
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(BASE + "/")
    page.wait_for_selector("#gateForm")
    page.fill("#gName", "listener")
    page.fill("#gPw", PASSWORD)
    page.click("#gateForm .primary")
    page.wait_for_selector("#gateForm", state="detached", timeout=30000)

    # installable: the manifest, its icons and a service worker that has taken over the page
    page.wait_for_function("navigator.serviceWorker.controller !== null", timeout=30000)
    cdp = ctx.new_cdp_session(page)
    problems = [e["errorId"] for e in cdp.send("Page.getInstallabilityErrors")["installabilityErrors"]]
    icons = {i["src"]: page.evaluate(f"""fetch({i["src"]!r}).then(r => r.blob()).then(createImageBitmap).then(b => [b.width, b.height])""")
             for i in page.evaluate("fetch('manifest.webmanifest').then(r => r.json())")["icons"] if i["type"] == "image/png"}
    print("installability:", problems or "no problems", "| icons:", icons)
    checks["installable: a worker, a manifest and its icons, no problems Chromium sees"] = (
        problems == [] and icons == {"icon-192.png": [192, 192], "icon-512.png": [512, 512], "icon-maskable.png": [512, 512]})

    # play an album; a song's ⋯ plays it next; another album's ⋯ adds it to the end
    album(page, "Harbor")
    page.click(".jtracks [data-local-play='0'].title")
    page.wait_for_function("!dock.audio.paused && dock.audio.currentTime > 0.2", timeout=30000)
    menu(page, ".jtracks [data-local-more='3']", "next")
    album(page, "Shore")
    menu(page, ".hero [data-local-more='all']", "end")
    page.click("#dockList")
    page.wait_for_selector("#queueList .qrow", timeout=5000)
    checks["play next goes after the song playing, add to queue at the end"] = page.evaluate(QUEUE) == ["Dawn", "Lantern", "Pier", "Gale", "Lantern", "Tidewater", "Undertow"]
    page.wait_for_timeout(500)
    page.screenshot(path=str(OUT / "queue-desk.png"))

    # reorder by dragging the grip (Undertow up two places), then with the keyboard (Pier down one)
    grip = page.locator("#queueList .qrow").nth(6).locator(".qgrip").bounding_box()
    h = page.locator("#queueList .qrow").nth(0).bounding_box()["height"]
    x, y = grip["x"] + grip["width"] / 2, grip["y"] + grip["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    for step in range(1, 11):
        page.mouse.move(x, y - 2 * h * step / 10)
    page.mouse.up()
    dragged = page.evaluate(QUEUE)
    page.focus("#queueList .qrow:nth-child(3) .qgrip")
    page.keyboard.press("ArrowDown")
    keyed = page.evaluate(QUEUE)
    checks["drag and arrow keys reorder it; the song playing carries on"] = (
        dragged == ["Dawn", "Lantern", "Pier", "Gale", "Undertow", "Lantern", "Tidewater"] and keyed == ["Dawn", "Lantern", "Gale", "Pier", "Undertow", "Lantern", "Tidewater"]
        and page.evaluate(NOW) == "Dawn" and not page.evaluate("dock.audio.paused"))
    page.click("#queueList .qrow:nth-child(7) [data-q-drop]")
    checks["✕ takes a song out"] = page.evaluate(QUEUE) == AFTER

    # shuffle: the song playing first, the rest moved; off puts the order back
    page.click("#queueShuffle")
    mixed = page.evaluate(QUEUE)
    page.click("#queueShuffle")
    checks["shuffle and back"] = (mixed[0] == "Dawn" and sorted(mixed) == sorted(AFTER)
                                  and page.evaluate(QUEUE) == AFTER
                                  and page.get_attribute("#queueShuffle", "aria-pressed") == "false")

    # the next song is already loaded when this one ends, and starts at once
    page.evaluate("""() => { window.gaps = [];
      for (const el of [dock.audio, dock.spare]) {
        el.addEventListener('ended', () => gaps.push(['ended', performance.now()]));
        el.addEventListener('playing', () => gaps.push(['playing', performance.now()]));
      } }""")
    to_end(page)
    page.wait_for_function("dock.spare.src.includes('Lantern') && dock.spare.readyState >= 3", timeout=10000)
    page.wait_for_function(f"{NOW} === 'Lantern' && !dock.audio.paused && dock.audio.currentTime > 0.1", timeout=15000)
    marks = page.evaluate("gaps")
    ended = next(t for k, t in marks if k == "ended")
    started = next((t for k, t in marks if k == "playing" and t >= ended), None)
    gap = (started - ended) if started else 9999
    print(f"gap between songs: {gap:.0f} ms")
    checks["the next song was loaded ahead and starts without a gap (< 150 ms)"] = gap < 150

    # the lock screen and headset know what's playing
    checks["the lock screen shows the song"] = page.evaluate("navigator.mediaSession.metadata?.title === 'Lantern' && navigator.mediaSession.metadata.artist === 'The Gulls'")

    # repeat one: the song starts again
    page.click("#queueRepeat")
    page.click("#queueRepeat")
    one = page.get_attribute("#queueRepeat", "aria-label")
    to_end(page, 0.6)
    page.wait_for_timeout(1500)
    checks["repeat one plays it again"] = one == "Repeat: one" and page.evaluate(NOW) == "Lantern" and page.evaluate("dock.audio.currentTime > 0.1 && dock.audio.currentTime < 2")
    page.click("#queueRepeat")

    # a reload: the queue is back, paused where it was, and the play button carries on from there
    page.evaluate("dock.audio.currentTime = 3")
    page.wait_for_timeout(300)
    page.evaluate("dock.audio.pause()")
    page.wait_for_timeout(300)
    page.reload()
    page.wait_for_selector("#dock:not([hidden])", timeout=30000)
    title_back = page.inner_text("#dockTitle")
    page.click("#dockList")
    page.wait_for_selector("#queueList .qrow", timeout=5000)
    order_back = page.evaluate(QUEUE)
    page.click("#dockPlay")
    page.wait_for_function("!dock.audio.paused && dock.audio.currentTime > 0", timeout=15000)
    resumed = page.evaluate("dock.audio.currentTime")
    checks["after a reload the queue is back and carries on where it was"] = (title_back == "Lantern" and order_back == AFTER
                                                                           and 2.5 <= resumed < 5)

    # save for offline, in 64 KB pieces: a Jellyfin album (its ⋯); its first song breaks off after the first piece,
    # and Try again carries on from there; a song from the music folder (its ⋯); a downloaded video (its save button)
    page.click("#queueClose")
    page.evaluate("dock.audio.pause()")
    playing = page.evaluate(NOW)
    page.evaluate("offChunk = 64 * 1024")
    jfs.BREAK.add("song1.mp3")
    page.goto(BASE + f"/#/jf/item/{jfs.ALBUM}")
    page.wait_for_selector("[data-album-more]", timeout=30000)
    menu(page, "[data-album-more]", "save")
    page.wait_for_function("offMine().some((e) => e.title === 'First Light' && e.error)", timeout=30000)
    broke = page.evaluate("offMine().find((e) => e.title === 'First Light')")
    page.goto(BASE + "/#/offline")
    page.wait_for_selector("[data-off-retry]", timeout=30000)
    page.click("[data-off-retry]")
    album(page, "Harbor")
    menu(page, ".jtracks [data-local-more='1']", "save")
    page.goto(BASE + "/#/yt/downloads")
    page.wait_for_selector("[data-voffline]", timeout=30000)
    page.click("[data-voffline]")
    page.wait_for_function("offMine().length === 4 && offMine().every((e) => e.done)", timeout=60000)
    ranges = [r for name, r in jfs.RANGES if name == "song1.mp3"]
    print("song1 pieces asked for:", ranges[:4], "| broke off at", broke["have"], broke["error"])
    checks["a download that breaks off carries on from where it stopped, byte for byte"] = (
        broke["have"] == 65536 and ranges.count("bytes=0-65535") == 1 and ranges.count("bytes=65536-131071") == 2
        and page.evaluate(SHA, "First Light") == hashlib.sha256((jfs.MEDIA / "song1.mp3").read_bytes()).hexdigest())
    checks["a Jellyfin album, a song from the music folder and a downloaded video are saved"] = sorted(page.evaluate("offMine().map((e) => e.title)")) == [
        "An offline one", "First Light", "Pier", "Second Wind"]

    # a film and an episode, saved from their Library pages: the film's text subtitles saved beside it (the picture-based
    # ones said not to be); an episode Jellyfin would have to convert says so and isn't saved
    jfs.NODIRECT.add(jfs.EP2)
    for item, word in ((jfs.FILM, "picture-based"), (jfs.EP1, "Saving"), (jfs.EP2, "convert")):  # what the toast says each time
        page.goto(BASE + f"/#/jf/item/{item}")
        page.wait_for_selector("[data-jf-save]", timeout=30000)
        page.click("[data-jf-save]")
        page.wait_for_function(f"toastEl.textContent.includes({word!r})", timeout=10000)
    jfs.NODIRECT.discard(jfs.EP2)
    page.wait_for_function("Object.values(offList()).every((e) => e.done || e.error)", timeout=60000)
    film = page.evaluate("offMine().find((e) => e.title === 'Night Train')")
    sub = page.evaluate(SUB)
    checks["a film and an episode save from their Library pages, text subtitles beside the film, what can't be said so"] = (
        sorted(e["title"] for e in page.evaluate("offMine().filter((e) => e.jf && e.kind === 'video')")) == ["Harbour Lights · S1:E1", "Night Train"]
        and page.evaluate(SHA, "Night Train") == hashlib.sha256((jfs.MEDIA / "film.mp4").read_bytes()).hexdigest()
        and page.evaluate(SHA, "Harbour Lights · S1:E1") == hashlib.sha256((jfs.MEDIA / "ep1.mp4").read_bytes()).hexdigest()
        and sub == jfs.VTT and len(film["subs"]) == 1)

    # no network: Parlor still opens, on its Offline page, with the queue in the dock; back online the bar goes
    ctx.set_offline(True)
    page.reload()
    page.wait_for_selector(".offbar", state="visible", timeout=30000)
    page.wait_for_selector("h1:has-text(\"You're offline\")", timeout=30000)
    page.wait_for_timeout(500)
    page.screenshot(path=str(OUT / "offline-desk.png"))
    checks["offline, the app opens on its Offline page with the queue in the dock"] = (
        page.evaluate("location.hash") == "#/offline" and page.inner_text("#dockTitle") == playing and page.locator("#gateForm").count() == 0)
    # the saved album plays in the dock from the device, seeking included; then the music folder's song; then the video
    page.click(".offalbum:has-text('Quiet Hours') [data-off-album]")
    page.wait_for_function("dock.audio.src.startsWith('blob:') && !dock.audio.paused && dock.audio.currentTime > 0.3", timeout=15000)
    page.evaluate("dock.audio.currentTime = 12")
    page.wait_for_function("dock.audio.currentTime > 12.3 && !dock.audio.paused", timeout=10000)
    checks["offline, the saved Jellyfin album plays from the device, seeking included"] = page.evaluate(NOW) == "First Light"
    page.click(".offalbum:has-text('Harbor') [data-off-play]")
    page.wait_for_function(f"{NOW} === 'Pier' && dock.audio.src.startsWith('blob:') && dock.audio.currentTime > 0.3", timeout=15000)
    page.click(".vcard[data-off]:has-text('An offline one') a.t")
    page.wait_for_selector("#offVideo", timeout=10000)
    page.wait_for_function("document.querySelector('#offVideo').currentTime > 0.3", timeout=15000)
    page.evaluate("document.querySelector('#offVideo').currentTime = 6")
    page.wait_for_function("document.querySelector('#offVideo').currentTime > 6.3", timeout=10000)
    page.wait_for_timeout(300)
    page.screenshot(path=str(OUT / "offline-video-desk.png"))
    checks["offline, the music folder's song plays, and the video with seeking"] = page.evaluate("dock.audio.paused")  # the video took over
    # the saved film (opened from the Offline page) plays in the Library player from the device: seeking, its subtitle
    # file, and the place it got to kept until Jellyfin answers; the episode plays too, with no subtitles to offer
    page.goto(BASE + "/#/offline")
    page.click(".vcard[data-off]:has-text('Night Train') a.t")
    page.wait_for_function("window.jfVideo?.src.startsWith('blob:') && jfVideo.currentTime > 0.3", timeout=15000)
    page.evaluate("jfVideo.currentTime = 8")
    page.wait_for_function("jfVideo.currentTime > 8.3 && !jfVideo.paused", timeout=10000)
    page.evaluate("jfVideo.pause()")  # its place is kept at 8 s (the film is 12 s long)
    page.select_option("#jfSubs", "0")
    page.wait_for_function("jfVideo.textTracks[0]?.activeCues?.length === 1", timeout=10000)
    cue = page.evaluate("jfVideo.textTracks[0].activeCues[0].text")
    page.screenshot(path=str(OUT / "offline-film-desk.png"))
    page.wait_for_function(f"(localStorage.getItem('parlor:jfpending') || '').includes('{jfs.FILM}')", timeout=10000)
    kept = json.loads(page.evaluate("localStorage.getItem('parlor:jfpending')"))
    checks["offline, the saved film plays from the device with seeking and its subtitles, and its place is kept"] = (
        cue == "The night train never stops" and [b["position"] for b in kept.values() if b["id"] == jfs.FILM] == [8])
    page.goto(BASE + "/#/offline")
    page.click(".vcard[data-off]:has-text('Harbour Lights') a.t")
    page.wait_for_function("window.jfVideo?.src.startsWith('blob:') && jfVideo.currentTime > 0.3", timeout=15000)
    checks["offline, the saved episode plays too"] = page.evaluate("jfVideo.duration > 5 && $('#jfSubs').hidden")
    # removing one frees its space
    page.goto(BASE + "/#/offline")
    page.wait_for_selector(".vcard[data-off] [data-off-drop]", timeout=10000)
    before = page.evaluate(OPFS)
    page.click(".vcard[data-off]:has-text('An offline one') [data-off-drop]")
    page.wait_for_selector(".vcard[data-off]:has-text('An offline one')", state="detached", timeout=10000)
    after = page.evaluate(OPFS)
    print(f"saved on the device: {before} bytes, {after} after removing the video ({(core.VIDEOS / rel).stat().st_size} bytes)")
    checks["removing one frees its space"] = before - after == (core.VIDEOS / rel).stat().st_size and len(page.evaluate("offMine()")) == 5
    jfs.DOWN["on"] = True  # back online with Jellyfin down: the place must wait, not be thrown away
    ctx.set_offline(False)
    page.wait_for_selector(".offbar", state="hidden", timeout=30000)  # checked every 10 s
    page.wait_for_function("ME && ME.name === 'listener' && document.body.dataset.offlineStart === undefined", timeout=30000)
    for _ in range(100):
        if jfs.DOWN["hits"]:
            break
        page.wait_for_timeout(100)
    page.wait_for_timeout(300)
    waiting = jfs.FILM in (page.evaluate("localStorage.getItem('parlor:jfpending')") or "")
    jfs.DOWN["on"] = False
    page.evaluate("jfFlush()")  # Jellyfin is back: the next flush sends it
    page.wait_for_function("!localStorage.getItem('parlor:jfpending') || localStorage.getItem('parlor:jfpending') === '{}'", timeout=10000)
    checks["back online with Jellyfin down the film's place waits"] = waiting and jfs.DOWN["hits"] > 0
    checks["back online the film's place reaches Jellyfin"] = (jfs.FILM, {"PlaybackPositionTicks": 8 * jfs.T}) in jfs.SAVES
    page.goto(BASE + "/#/settings")
    page.wait_for_selector("#deviceText", timeout=30000)
    checks["back online the bar goes and Parlor carries on"] = "Install" in page.inner_text("#deviceText") or "browser" in page.inner_text("#deviceText")

    # the phone: the queue is a sheet over the dock, no sideways scroll
    phone = b.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark", is_mobile=True, has_touch=True)
    phone.add_cookies(ctx.cookies())
    pp = phone.new_page()
    pp.on("pageerror", lambda e: errors.append(f"phone: {e}"))
    pp.goto(BASE + "/#/library/The%20Gulls/Harbor")
    pp.wait_for_selector(".jtracks [data-local-more]", timeout=30000)
    pp.click(".jtracks [data-local-play='1'].title")
    pp.wait_for_function("!dock.audio.paused", timeout=30000)
    pp.click(".jtracks [data-local-more='3']")
    pp.wait_for_selector("#queueMenu", timeout=5000)
    pp.wait_for_timeout(300)
    pp.screenshot(path=str(OUT / "menu-phone.png"))
    pp.click("#queueMenu [data-qm=next]")
    pp.click("#dockList")
    pp.wait_for_selector("#queueList .qrow", timeout=5000)
    pp.wait_for_timeout(500)
    pp.screenshot(path=str(OUT / "queue-phone.png"))
    box = pp.locator("#queuePanel").bounding_box()
    print("phone sheet:", box, pp.locator("#dock").bounding_box(), pp.evaluate("[document.documentElement.scrollWidth, innerWidth]"))
    checks["on a phone the queue is a sheet above the dock, no sideways scroll"] = (
        pp.evaluate("document.documentElement.scrollWidth <= innerWidth + 1") and box["width"] >= 389
        and box["y"] + box["height"] <= pp.locator("#dock").bounding_box()["y"] + 1)
    b.close()

    # on plain http at a name other than this computer, Settings says install and offline need https, and no worker runs
    b = pw.chromium.launch(executable_path=str(SHELL) if SHELL else None, args=["--host-resolver-rules=MAP parlor.test 127.0.0.1"])
    http = b.new_context(viewport={"width": 1366, "height": 900}, color_scheme="dark").new_page()
    http.on("pageerror", lambda e: errors.append(f"http: {e}"))
    http.goto(f"http://parlor.test:{PORT}/")
    http.wait_for_selector("#gateForm")
    http.fill("#gName", "listener")
    http.fill("#gPw", PASSWORD)
    http.click("#gateForm .primary")
    http.wait_for_selector("#gateForm", state="detached", timeout=30000)
    http.goto(f"http://parlor.test:{PORT}/#/settings")
    http.wait_for_selector("#deviceText", timeout=30000)
    http.locator("#devicePanel").scroll_into_view_if_needed()
    http.wait_for_timeout(400)
    http.screenshot(path=str(OUT / "settings-http.png"))
    checks["on plain http Settings says how to get https, and no worker runs"] = (
        "secure address" in http.inner_text("#deviceText") and "tailscale serve" in http.inner_text("#deviceText")
        and http.evaluate("!window.isSecureContext && !('serviceWorker' in navigator && navigator.serviceWorker.controller)"))
    b.close()
server.should_exit = True
for k, v in checks.items():
    print(f"  {'✓' if v else '✗'} {k}")
print("page errors:", errors or "none")
shots = sorted(x.name for x in OUT.glob("*.png"))
print("screenshots:", ", ".join(shots))
ok = len(checks) == 23 and all(checks.values()) and not errors and len(shots) == 7
print("RESULT:", "ok" if ok else "FAIL")
sys.exit(0 if ok else 1)
