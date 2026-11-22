"""Every page of Parlor in a real browser, at computer, tablet and phone width, in both themes. Each must open with
no page error and no sideways scroll, show at most one row of links to other pages, and repeat no link the sidebar
already shows. The outside services are the README pictures' stand-ins (tests/readme_shots.py), YouTube's feeds are
answered here, and nothing leaves this machine. Screenshots, and contact sheets to look through, land in OUT:
   python tests/ui_sweep.py [OUT_DIR [PAGE ...]]   (needs Playwright and its Chromium; PAGE names keep the run to those pages)"""
import json
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

if len(sys.argv) < 2:
    sys.argv.append(tempfile.mkdtemp(prefix="ui-sweep-"))  # readme_shots writes where its first argument says
import readme_shots as rs  # noqa: E402  (the stand-ins and their made-up art)
from PIL import Image, ImageDraw  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

OUT = rs.OUT
WIDTHS = {"desk": (1366, 900, False), "tablet": (820, 1180, False), "phone": (390, 844, True)}
THUMB = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 9'><defs><linearGradient id='g' x2='1' y2='1'><stop stop-color='%s'/>"
         "<stop offset='1' stop-color='%s'/></linearGradient></defs><rect width='16' height='9' fill='url(#g)'/></svg>")
VIDEOS = [{"id": f"sweepvid{i:03d}", "title": t, "channel": c, "channel_id": f"UC{i:022d}", "duration": 300 + 97 * i, "views": 1200 * (i + 1), "published": time.time() - 86400 * (i + 1),
           "thumb": "data:image/svg+xml," + (THUMB % rs.PALETTE[i % len(rs.PALETTE)][:2]).replace("#", "%23")}
          for i, (t, c) in enumerate([("Restoring a harbour clock", "Tin Workshop"), ("A week on the night ferry", "Slow Orbit"), ("Bread in a cast iron pot", "Kettle & Crow"),
                                      ("How lighthouses talk", "Field Notes"), ("Sketching rooftops in the rain", "Paper Sun"), ("The quarry at dawn", "Field Notes"),
                                      ("Fixing a film camera", "Tin Workshop"), ("Salt flats by bicycle", "Slow Orbit"), ("Twelve chords, one song", "The Lamps"),
                                      ("A small boat, a long river", "Slow Orbit"), ("Maps drawn from memory", "Paper Sun"), ("Winter soup, three ways", "Kettle & Crow")])]
TILE = [{"id": f"sweepalbum{i}", "name": name, "artists": [{"name": artist}], "image": VIDEOS[i]["thumb"]} for i, (name, artist) in enumerate(rs.ALBUMS)]
FEEDS = {"**/api/yt/feed*": {"items": VIDEOS, "channels": 5, "failed": []}, "**/api/yt/recommended": {"items": VIDEOS[::-1], "source": "local"},
         "**/api/yt/related/*": {"items": VIDEOS}, "**/api/yt/search*": {"items": [{**v, "type": "video"} for v in VIDEOS]},
         "**/music/api/home": {"new": TILE, "chart": [], "playlists": []}, "**/music/api/search*": {"tracks": [], "artists": [], "albums": TILE, "playlists": []}}
# where, and what must be on the page before it is judged
PAGES = [("home", "#/", "main h1"), ("search", "#/search/night", ".vcard"),
         ("library", "#/jf", ".jcard"), ("films", "#/jf/movies", ".jcard"), ("shows", "#/jf/shows", ".jcard"), ("albums", "#/jf/music", ".jcard"), ("books", "#/jf/books", ".jcard"),
         ("film", f"#/jf/item/{rs.jfs.FILM}", ".hero .primary"), ("games", "#/games", "main h1"),
         ("watch", "#/yt/home", ".vcard"), ("subs", "#/yt/subs", ".vcard"), ("live", "#/live", "main h1"), ("recommended", "#/yt/rec", ".vcard"),
         ("music", "#/music", ".tile"), ("your-music", "#/library", "main h1"), ("playlists", "#/playlists", "main h1"),
         ("request", "#/request", ".pcard[data-book]"), ("ask-search", "#/request/search/lantern", ".pcard"),
         ("ask-films", "#/media", ".pcard"), ("ask-music", "#/lidarr", "main h1"), ("ask-games", "#/gamereq", ".pcard[data-game]"), ("ask-books", "#/bookreq", ".pcard[data-book]"),
         ("asked", "#/requests", "main h1"), ("asked-films", "#/requests/mine", "main .picks"), ("collections", "#/sets", "main h1"), ("downloads", "#/torrents", "main h1"),
         ("you", "#/you", ".you-tile"), ("continue", "#/yt/continue", "main h1"), ("later", "#/yt/later", "main h1"), ("history", "#/yt/history", "main h1"),
         ("saved", "#/yt/downloads", "main h1"), ("offline", "#/offline", "main h1"), ("tvs", "#/tvs", "main h1"), ("channels", "#/yt/channels", "main h1"),
         ("settings", "#/settings", "main h1")]
PAGES = [x for x in PAGES if not sys.argv[2:] or x[0] in sys.argv[2:]]
if not PAGES:
    sys.exit(f"no page is called {' or '.join(sys.argv[2:])}")  # a mistyped name would check nothing and say ok
# what the browser reads off a page: its width, its rows of links to other pages, and links the sidebar shows too
LOOK = """() => {
  const seen = (e) => !!e.offsetParent && getComputedStyle(e).visibility !== "hidden";
  const rows = [...document.querySelectorAll("main nav.chips")].filter(seen).filter((n) => !n.closest("dialog, .hero"));
  const side = [...document.querySelectorAll("#side a")].filter(seen).map((a) => a.getAttribute("href"));
  return { wide: document.documentElement.scrollWidth > innerWidth + 1,
           rows: rows.map((n) => [...n.querySelectorAll("a")].map((a) => a.textContent.trim()).join(" | ")).filter(Boolean),
           twice: [...new Set(rows.flatMap((n) => [...n.querySelectorAll("a")].map((a) => a.getAttribute("href"))).filter((h) => side.includes(h)))],
           title: document.querySelector("main h1")?.textContent.trim() || "" };
}"""


def sheet(names, label):
    """the shots of one width and theme, side by side and named, six to a sheet"""
    for n in range(0, len(names), 6):
        ims = [Image.open(OUT / f"{name}-{label}.png") for name in names[n:n + 6]]
        w = 640 if ims[0].width > 500 else 320
        thumbs = [im.resize((w, im.height * w // im.width)) for im in ims]
        cols = 3 if w == 640 else 6
        h = max(t.height for t in thumbs) + 24
        out = Image.new("RGB", (cols * (w + 8), h * -(-len(thumbs) // cols)), "#808080")
        for i, (t, name) in enumerate(zip(thumbs, names[n:n + 6])):
            x, y = (i % cols) * (w + 8), (i // cols) * h
            out.paste(t, (x, y + 24))
            ImageDraw.Draw(out).text((x + 4, y + 4), name, fill="#ffffff", font=rs.font(15))
        out.save(OUT / f"sheet-{label}-{n // 6 + 1}.png")


def main():
    rs.core.db()
    rs.auth.create_user("maya", rs.PASSWORD, True)
    rs.jfs.point(rs.core)
    rs.stock_library()
    steam = rs.stock_games()
    rs.stock_live()
    rs.stock_game_requests()
    rs.stock_book_requests()
    rs.stock_sets()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = rs.uvicorn.Server(rs.uvicorn.Config(rs.env.yeerr.app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.1)
    base = f"http://127.0.0.1:{port}"
    shell = next(iter(sorted(Path.home().glob(".cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"))), None)
    errors, found, bad = [], {}, []
    pictures = (("steamstatic.com", rs.game_picture), ("covers.openlibrary.org", rs.book_cover), ("image.tmdb.org", rs.set_picture))

    def outside(route):
        url = route.request.url
        paint = next((paint for host, paint in pictures if host in url), None)
        return paint(route) if paint else route.continue_() if "//fonts.g" in url else route.abort()

    try:
        with sync_playwright() as pw:
            b = pw.chromium.launch(executable_path=str(shell) if shell else None)
            for label, (width, height, mobile) in WIDTHS.items():
                ctx = b.new_context(viewport={"width": width, "height": height}, color_scheme="dark", is_mobile=mobile, has_touch=mobile)
                ctx.route("https://**/*", outside)
                for path, body in FEEDS.items():  # what the server would ask YouTube and Spotify for: answered here
                    ctx.route(path, (lambda body: lambda r: r.fulfill(content_type="application/json", body=body))(json.dumps(body)))
                p = ctx.new_page()
                p.on("pageerror", lambda e, label=label: errors.append(f"{label} {p.url.split('#')[-1]}: {e}"))
                p.goto(base + "/")
                p.wait_for_selector("#gateForm")
                p.fill("#gName", "maya")
                p.fill("#gPw", rs.PASSWORD)
                p.click("#gateForm .primary")
                p.wait_for_selector("#gateForm", state="detached", timeout=30000)
                for name, where, wait in PAGES:
                    p.goto("about:blank")
                    p.goto(base + "/" + where)
                    try:
                        p.wait_for_selector("main " + wait if not wait.startswith("main") else wait, timeout=20000)
                    except Exception:
                        bad.append(f"{label} {name}: never showed {wait}")
                    p.wait_for_timeout(700)
                    saw = found[f"{name}-{label}"] = p.evaluate(LOOK)
                    if saw["wide"]:
                        bad.append(f"{label} {name}: scrolls sideways")
                    if len(saw["rows"]) > 1:
                        bad.append(f"{label} {name}: {len(saw['rows'])} rows of page links: {saw['rows']}")
                    if saw["twice"]:
                        bad.append(f"{label} {name}: links the sidebar already shows: {saw['twice']}")
                    if name == "home":  # the rows stay put whatever the hero shows: its tallest form and its shortest
                        tall = """tvhHero({ title: 'Continue watching', accent: 'var(--amber)' }, { title: 'Spider-Man: Far From Home, the Extended Fan Cut', kind: 'Film',
                          meta: ['2019', '2h 9m', 'PG-13', 'Action · Adventure · Science Fiction'], over: 'Peter Parker and his friends go on a summer trip to Europe. '.repeat(6),
                          go: 'Resume', pct: .4, href: '#/' })"""
                        short = "tvhHero({ title: 'Trending' }, { title: 'Owl', kind: 'Video', meta: [], go: 'Play', href: '#/' })"
                        tops = []
                        for draw in (tall, short):
                            p.evaluate(draw)
                            p.wait_for_timeout(450)
                            tops.append(round(p.evaluate("document.querySelector('#hhRows').getBoundingClientRect().top + scrollY")))
                        if tops[0] != tops[1]:
                            bad.append(f"{label} home: the rows moved {tops[0] - tops[1]} px when the hero changed")
                        p.evaluate("route()")  # and back to the page's own first title
                        p.wait_for_function("document.querySelector('#tvhHero h1')?.textContent !== 'Owl'", timeout=5000)
                    if name == "home" and not mobile:  # Home's hero shows the title the pointer rests on, then the one the keyboard moves to
                        hero = "document.querySelector('#tvhHero h1')?.textContent"
                        try:
                            p.hover("#hhRows a.tvt >> nth=1")
                            p.wait_for_function(f"{hero} === document.querySelectorAll('#hhRows a.tvt')[1].getAttribute('aria-label')", timeout=3000)
                            p.focus("#hhRows a.tvt >> nth=0")
                            p.keyboard.press("ArrowDown")
                            p.wait_for_function(f"document.activeElement.matches('#hhRows .tvh-row:nth-child(2) a.tvt') && {hero} === document.activeElement.getAttribute('aria-label')", timeout=3000)
                            asked = []  # Continue watching's x asks the server and stays on Home (answered here, so the row is still there for the next width)
                            p.route("**/api/jf/progress", lambda r: (asked.append(r.request.post_data_json), r.fulfill(json={})))
                            p.hover("#hhRows a.tvt >> nth=0")
                            p.click("#hhRows a.tvt >> nth=0 >> .tvt-drop")
                            p.wait_for_timeout(400)
                            p.unroute("**/api/jf/progress")
                            if not (asked and asked[0].get("position") == 0 and p.url.endswith("#/")):
                                bad.append(f"{label} home: Continue watching's x asked {asked} and left the page at {p.url.split('#')[-1]}")
                            p.wait_for_selector("#hhRows a.tvt")
                            p.mouse.move(0, 0)
                        except Exception as err:
                            bad.append(f"{label} home: the hero didn't follow the pointer and the arrow keys ({str(err).splitlines()[0][:90]}; hero {p.evaluate(hero)!r})")
                    for scheme in ("dark", "light"):
                        p.emulate_media(color_scheme=scheme)
                        p.wait_for_timeout(150)
                        p.screenshot(path=str(OUT / f"{name}-{label}-{scheme}.png"))
                    p.emulate_media(color_scheme="dark")
                ctx.close()
            b.close()
    finally:
        server.should_exit = True
        steam.shutdown()
        rs.jfs.JF.shutdown()
    for label in WIDTHS:
        for scheme in ("dark", "light"):
            sheet([name for name, *_ in PAGES], f"{label}-{scheme}")
    (OUT / "found.json").write_text(json.dumps(found, indent=1))
    shots = len(list(OUT.glob("*-dark.png"))) + len(list(OUT.glob("*-light.png")))
    print(f"{len(PAGES)} pages × {len(WIDTHS)} widths × 2 themes: {shots} screenshots in {OUT}")
    for line in bad:
        print("  ✗", line)
    print("page errors:", errors or "none")
    ok = not bad and not errors and shots == len(PAGES) * len(WIDTHS) * 2
    print("RESULT:", "ok" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
