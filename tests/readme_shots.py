"""The README's screenshots, made from the running app with made-up demo data and nothing else: Parlor runs here against
the test stand-ins (a Jellyfin with posters, a Steam, retro games, a TV paired to a phone, a Seerr with franchises) and each page is shot at
desktop (1280 wide) and phone (390 wide) width, dark and light, into docs/assets/screens/NAME-VIEW-SCHEME.png, each
file kept under the README check's 400 KB budget. No real library, name, address or key is used or shown.

Run from app/:  ../.venv/bin/python ../tests/readme_shots.py [OUT_DIR]   (needs Playwright, its Chromium, ffmpeg and Pillow)"""
import io
import json
import re
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import auth
import bulk
import core
import games
import jf_standin as jfs
import live_standin  # noqa: F401  (YouTube and Twitch, standing in, for Watch → Live)
import seerr
import uvicorn
from jf_standin import T
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright
from seerr_standin import Seerr as SeerrStandin

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "assets" / "screens"
OUT.mkdir(parents=True, exist_ok=True)
PASSWORD = "Readme-demo-pass-42!"  # pragma: allowlist secret (made up)
KB_BUDGET = 380  # the README check allows 400 KB a PNG
VIEWS = {"desktop": dict(viewport={"width": 1280, "height": 800}), "phone": dict(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)}


# ---------------------------------------------------------------- made-up art: flat shapes, a title, small as PNG

PALETTE = [("#243b6b", "#ffb547", "#101a33"), ("#4a2c5e", "#6be0c4", "#241431"), ("#6b2b34", "#ffc56e", "#35141a"), ("#1f4d4a", "#ff6b6b", "#0f2827"),
           ("#37306b", "#6cc2ff", "#1a1638"), ("#5a4326", "#a58bff", "#2b2013"), ("#2a4a2e", "#ffb547", "#142617"), ("#5a2f52", "#6cc2ff", "#2c1628")]


def font(size):
    return ImageFont.load_default(size)


def art(title, w, h, n, subtitle="", pad=0.08):
    """a flat poster: a sky, a sun, two hills and the title"""
    bg, sun, hill = PALETTE[n % len(PALETTE)]
    im = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(im)
    r = int(min(w, h) * (0.22 + 0.03 * (n % 3)))
    cx, cy = int(w * (0.3 + 0.13 * (n % 5))), int(h * 0.38)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=sun)
    d.ellipse([-w * 0.3, h * 0.62, w * 0.85, h * 1.5], fill=hill)
    d.ellipse([w * 0.3, h * 0.72, w * 1.4, h * 1.6], fill=_shade(hill))
    size = int(min(w, h * 0.7) * 0.115)
    words, lines, line = title.split(), [], ""
    for word in words:
        if d.textlength(f"{line} {word}".strip(), font=font(size)) > w * (1 - 2 * pad) and line:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    lines.append(line)
    y = h - int(h * 0.08) - len(lines) * int(size * 1.25) - (int(size * 0.8) if subtitle else 0)
    for ln in lines:
        d.text((int(w * pad), y), ln, fill="#f7f3ea", font=font(size))
        y += int(size * 1.25)
    if subtitle:
        d.text((int(w * pad), y), subtitle, fill="#d6cfe0", font=font(int(size * 0.62)))
    return im


def _shade(hex_):
    r, g, b = (int(hex_[i:i + 2], 16) for i in (1, 3, 5))
    return f"#{int(r * .75):02x}{int(g * .75):02x}{int(b * .75):02x}"


def save_art(name, im, jpeg=False):
    p = jfs.MEDIA / name
    im.save(p, "JPEG" if jpeg else "PNG", **({"quality": 88} if jpeg else {"optimize": True}))
    return p


# ---------------------------------------------------------------- the Jellyfin stand-in, stocked

FILMS = [("Night Train", 2024, "Mystery"), ("The Salt Road", 2023, "Drama"), ("Paper Lanterns", 2022, "Fantasy"), ("Seventh Harbour", 2024, "Thriller"),
         ("Quiet Engines", 2021, "Science fiction"), ("Winter Orchard", 2023, "Drama"), ("Glass Meridian", 2025, "Adventure"), ("A Small Weather", 2020, "Comedy"),
         ("Copper Bay", 2022, "Western"), ("The Long Lamp", 2019, "Documentary")]
SHOWS = ["Harbour Lights", "Tin Kingdom", "Field Notes", "Midnight Ferry", "The Quarry Club", "Slow Orbit"]
ALBUMS = [("Quiet Hours", "The Lamps"), ("Low Tide Radio", "Marlow & Wren"), ("Paper Sun", "Ines Okoro"), ("Northern Lines", "The Gulls"),
          ("Kite Season", "Hollow Pines"), ("Slow Orbit", "Cass Delamere")]
BOOKS = ["The Keeper", "Salt & Ledger", "An Orchard Year", "The Cartographer's Wife"]


ART: dict = {}  # item id -> its poster, wide and square pictures


def stock_library():
    items = jfs.ITEMS
    keep = {k: items[k] for k in (jfs.SEASON, jfs.EP1, jfs.EP2, jfs.S1, jfs.S2, jfs.ARTIST)}
    items.clear()
    items.update(keep)
    n = 0

    def add(iid, name, kind, view, title_art, **more):
        nonlocal n
        n += 1
        items[iid] = {"Id": iid, "Name": name, "Type": kind, "_view": view, "ImageTags": {"Primary": "p"}, **more}
        ART[iid] = {"poster": save_art(f"a{n}-p.png", art(name, 400, 600, n)), "wide": save_art(f"a{n}-w.png", art(name, 960, 540, n)),
                    "square": save_art(f"a{n}-s.png", art(name, 500, 500, n, title_art))}
        if kind in ("Movie", "Series"):
            items[iid]["BackdropImageTags"] = ["b"]
        return items[iid]

    for i, (name, year, genre) in enumerate(FILMS):
        film = add(jfs.FILM if i == 0 else jfs.hid(0x300 + i), name, "Movie", jfs.V_FILMS, "", ProductionYear=year, RunTimeTicks=(88 + 7 * i) * 60 * T,
                   Genres=[genre, "Feature"], CommunityRating=round(6.6 + (i * 0.37) % 2.2, 1), OfficialRating=["PG", "PG-13", "R"][i % 3], _file="film.mp4",
                   Overview="A sleeper car, a missing ticket and a conductor who has seen it all before." if i == 0 else "A quiet story about a small place and the people who keep it going.")
        if i == 0:
            film["People"] = [{"Name": "Ada Brook", "Role": "Conductor"}, {"Name": "Tomas Reyes", "Role": "The passenger"}]
    for i, name in enumerate(SHOWS):
        add(jfs.SERIES if i == 0 else jfs.hid(0x400 + i), name, "Series", jfs.V_SHOWS, "", ProductionYear=2021 + i % 4,
            Overview="A small port, a long winter." if i == 0 else "A new season, the same neighbours.")
    for iid in (jfs.SEASON, jfs.EP1, jfs.EP2):
        ART[iid] = ART[jfs.SERIES]
    for i, (name, artist) in enumerate(ALBUMS):
        add(jfs.ALBUM if i == 0 else jfs.hid(0x500 + i), name, "MusicAlbum", jfs.V_MUSIC, artist, AlbumArtist=artist, Artists=[artist], ProductionYear=2019 + i)
    for iid in (jfs.S1, jfs.S2, jfs.ARTIST):
        ART[iid] = ART[jfs.ALBUM]
    for i, name in enumerate(BOOKS):
        add(jfs.BOOK if i == 0 else jfs.hid(0x600 + i), name, "Book", jfs.V_BOOKS, "", ProductionYear=2018 + i, Overview="A lighthouse, a ledger.", _file="book.epub")
    for v in jfs.VIEWS:
        ART[v["Id"]] = ART[jfs.FILM]
    # picked up where each was left: Continue watching
    for iid, at in ((jfs.hid(0x303), 0.35), (jfs.hid(0x305), 0.72)):
        jfs.STATE[iid] = {"PlaybackPositionTicks": int(items[iid]["RunTimeTicks"] * at)}
    # the stand-in serves one poster for everything: serve each item's own
    handler = jfs.StandIn
    original = handler.do_GET

    def do_get(self):
        m = re.fullmatch(r"/Items/(\w+)/Images/(\w+)(?:/\d+)?", urlparse(self.path).path)
        if m and f'Token="{jfs.KEY}"' in (self.headers.get("Authorization") or ""):
            it = items.get(m[1]) or next((v for v in jfs.VIEWS if v["Id"] == m[1]), None)
            if it and m[1] in ART:
                a = ART[m[1]]
                kind = "wide" if "Backdrop" in m[2] or it.get("Type") == "Episode" or it.get("Type") == "CollectionFolder" else "square" if it["Type"] in ("MusicAlbum", "Audio", "MusicArtist") else "poster"
                return self.file(a[kind], "image/png")
        return original(self)

    handler.do_GET = handler.do_HEAD = do_get


# ---------------------------------------------------------------- Steam and the retro games

SAMPLE = [(1001, "Lantern Run", 4300, 310), (1002, "Orbital Ferry", 2600, 0), (1003, "Salt Flats Racing", 1900, 120), (1004, "Kettle & Crow", 1500, 0),
          (1005, "Tiny Harbour", 980, 45), (1006, "Moss Kingdom", 760, 0), (1007, "Paper Pilots", 540, 0), (1008, "Deep Library", 410, 0),
          (1009, "Night Courier", 320, 0), (1010, "Hollow Pines", 210, 0), (1011, "Clockwork Orchard", 150, 0), (1012, "Slow Orbit", 90, 0)]
STEAM_ID, KEY = "76561197960287930", "0123456789ABCDEF0123456789ABCDEF"  # pragma: allowlist secret (made up)


class Steam(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        u, q = urlparse(self.path), {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
        if u.path.startswith("/img/"):
            appid = int(u.path.split("/")[2])
            buf = io.BytesIO()
            art(dict((a, n) for a, n, *_ in SAMPLE)[appid], 460, 215, appid, pad=0.06).save(buf, "JPEG", quality=85)
            body, kind, status = buf.getvalue(), "image/jpeg", 200
        else:
            kind, status = "application/json", 200 if q.get("key") == KEY else 403
            body = json.dumps({"response": {
                "/ISteamUser/GetPlayerSummaries/v2/": {"players": [{"personaname": "Maya", "communityvisibilitystate": 3}]},
                "/IPlayerService/GetOwnedGames/v1/": {"game_count": len(SAMPLE), "games": [{"appid": a, "name": n, "playtime_forever": m} for a, n, m, _ in SAMPLE]},
                "/IPlayerService/GetRecentlyPlayedGames/v1/": {"games": [{"appid": a, "name": n, "playtime_2weeks": r, "playtime_forever": m} for a, n, m, r in SAMPLE if r]},
            }.get(u.path, {})}).encode()
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


RETRO = {"nes": [("Pixel Garden", "nes"), ("Block Drop", "nes"), ("Mole Patrol", "nes")], "gba": [("Tiny Quest", "gba"), ("Cave Runner", "gba"), ("Star Kitchen", "gba")],
         "snes": [("Moon Courier", "sfc"), ("Rail Baron", "sfc")]}


def stock_games():
    for system, titles in RETRO.items():
        folder = env.TMP / "games" / "roms" / system
        folder.mkdir(parents=True, exist_ok=True)
        for i, (title, ext) in enumerate(titles):
            (folder / f"{title}.{ext}").write_bytes(b"\0" * 192)
            art(title, 300, 300, i + len(system) * 3, system.upper()).save(folder / f"{title}.png", optimize=True)
    games.GAMES = env.TMP / "games"
    steam = ThreadingHTTPServer(("127.0.0.1", 0), Steam)
    threading.Thread(target=steam.serve_forever, daemon=True).start()
    games.STEAM_API = f"http://127.0.0.1:{steam.server_port}"
    games.STEAM_ART = f"http://127.0.0.1:{steam.server_port}/img/{{appid}}/{{kind}}.jpg"
    games.STREAM = "http://moonlight-web:8080"  # only so the page says the bridge is on; nothing is fetched from it
    core.set_setting("games_stream_admin", "1")
    return steam


# ---------------------------------------------------------------- shooting

# ---------------------------------------------------------------- Twitch, standing in, dressed

LIVE = [("speedy", "Speedrun practice", "Retro", 321), ("nightowl", "Late-night lo-fi to read to", "Music", 1840),
        ("tinkerer", "Building a tiny synth from scratch", "Science & Technology", 212), ("panfry", "Sunday pancakes, live", "Food & Drink", 96)]
CHAT = [("Ravi", "#FFB547", "that jump was clean"), ("mossy", "#6BE0C4", "first time catching this live"), ("Juno", "#A58BFF", "what route is this?"),
        ("pip", "#6CC2FF", "the music is so good"), ("Teodor", "#FF6B6B", "GG"), ("leafy", "#6BE0C4", "one more run!")]


def stock_live():
    def png(im):
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
        return buf.getvalue()

    for n, (login, title, game, viewers) in enumerate(LIVE):
        live_standin.STREAMS[login] = {"title": title, "viewersCount": viewers, "game": game}
        live_standin.IMAGES[f"{login}-preview.png"] = png(art(title, 640, 360, n + 3))
        live_standin.IMAGES[f"{login}-avatar.png"] = png(art(login[0].upper(), 150, 150, n + 3, pad=0.3))
        live_standin.IMAGES[f"{login}-vod.png"] = png(art("Last night's runs", 320, 180, n + 4))
        live_standin.IMAGES[f"{login}-clip.png"] = png(art("Big moment", 320, 180, n + 5))
    live_standin.CHAT[:] = CHAT
    info = live_standin.yt.video_info
    live_standin.yt.video_info = lambda *a, **k: {**info(*a, **k), "description": "Soft rain and slow songs, all night. Requests in the chat."}
    picture = live_standin.SEG / "speedy.png"
    art(LIVE[0][1], 640, 360, 3).save(picture)
    live_standin.make_segments("live", ["-loop", "1", "-i", str(picture)])


# ---------------------------------------------------------------- Seerr, standing in, for Request → Films & shows → Collections

LANTERN = ["The Lantern Keeper", "Lantern Light", "The Paper Lantern", "Last Lantern Home", "Lantern: Embers"]
SETS = {101: "Night Train Collection", 102: "The Salt Road Trilogy", 103: "Glass Meridian Collection", 104: "Copper Bay Collection"}
STUDIOS = {7: "Harbour", 11: "Tin Lantern", 12: "Kestrel & Co", 13: "Northlight", 14: "Paper Moon"}
NETWORKS = {9: "Tin Channel", 21: "Gull TV", 22: "Meridian+", 23: "Orchard", 24: "Ferry One"}
SET_ART: dict = {}  # a picture's path on the image host -> the title drawn on it


def stock_sets():
    """a franchise half in the library with one film requested, more franchises, studios and networks, each with a picture;
    Seerr's own rows and its search answer with the franchise's five films"""
    fake, real = SeerrStandin(), seerr.requests.request
    for film, title in zip(fake.collection, LANTERN):
        film.update(title=title, posterPath=f"/poster-{film['id']}.png")
        SET_ART[film["posterPath"]] = title
    fake.status.update({("movie", 1001): 5, ("movie", 1002): 5, ("movie", 1003): 2})
    SET_ART.update({"/set-100.png": "The Lantern Saga", "/wide-100.png": "The Lantern Saga", **{f"/set-{i}.png": n for i, n in SETS.items()}})

    def answer(method, url, **kw):
        if "seerr.test" not in url:
            return real(method, url, **kw)
        kind, _, sid = url.split("/api/v1/", 1)[1].partition("/")
        if kind.split("?")[0] == "search" or kind == "discover" and not {"studio", "network", "keywords"} & set(kw.get("params") or {}):
            body = {"page": 1, "totalPages": 1, "totalResults": 5, "results": [{**film, "mediaType": "movie", "backdropPath": "/wide-100.png", "releaseDate": film["releaseDate"].replace("2999", "2027")}  # the stand-in's far-off film: next year's
                                                                                 for film in fake.collection]}
        elif kind == "collection" and sid.isdigit() and int(sid) in SETS:
            body = {"id": int(sid), "name": SETS[int(sid)], "posterPath": f"/set-{sid}.png", "parts": []}
        elif kind in ("studio", "network") and sid.isdigit() and int(sid) in (STUDIOS if kind == "studio" else NETWORKS):
            body = {"id": int(sid), "name": (STUDIOS if kind == "studio" else NETWORKS)[int(sid)], "logoPath": f"/logo-{kind}-{sid}.png"}
        else:
            r = SeerrStandin.__call__(fake, method, url, **kw)
            if (kind, sid) == ("collection", "100"):
                r.json().update(posterPath="/set-100.png", backdropPath="/wide-100.png", overview="Five films about a lighthouse keeper and the harbour town that forgot her.")
            return r
        return mock.Mock(ok=True, status_code=200, content=b"x", text="", json=lambda: body)

    mock.patch.object(seerr.requests, "request", answer).start()
    bulk.FRANCHISES, bulk.STUDIOS, bulk.NETWORKS = tuple(SETS), tuple(STUDIOS), tuple(NETWORKS)
    core.set_setting("seerr_url", "http://seerr.test")
    core.set_setting("seerr_key", "made-up")
    core.db().execute("UPDATE users SET seerr_id = 7 WHERE name = 'maya'")


def set_picture(route):
    """what the image host would send: a poster with the title on it, or a studio's wordmark in the theme's ink"""
    url = route.request.url
    path = "/" + url.rsplit("/", 1)[1]
    if path.startswith("/logo-"):
        kind, sid = path[6:-4].split("-")
        ink = "#1c1a24" if "1c1a24" in url else "#ffffff"
        return route.fulfill(content_type="image/svg+xml", body=f"<svg xmlns='http://www.w3.org/2000/svg' width='260' height='60' viewBox='0 0 260 60'><text x='130' y='41' text-anchor='middle' "
                             f"font-family='Georgia, serif' font-weight='700' font-size='30' letter-spacing='1' fill='{ink}'>{(STUDIOS if kind == 'studio' else NETWORKS)[int(sid)].replace('&', '&amp;')}</text></svg>")
    wide = path.startswith("/wide-")
    out = io.BytesIO()
    art(SET_ART.get(path, ""), 960 if wide else 342, 540 if wide else 513, sum(map(ord, path))).save(out, "PNG")
    route.fulfill(content_type="image/png", body=out.getvalue())


def stock_game_requests():
    """Request → Games: the sample games in a stand-in Steam store, Questarr's login as setup makes it, two requests under way"""
    import gamelookup
    import stack
    import test_gamelookup as shop

    day = 86400
    shop.SHOP[:] = [shop.item(a, n, [19], when=shop.NOW + 20 * day if i > 9 else shop.NOW - (6 if i > 5 else 300 + 200 * i) * day) for i, (a, n, *_) in enumerate(SAMPLE)]
    for obj, name, value in ((gamelookup, "steam", shop.Steam()), (gamelookup, "_now", lambda: shop.NOW), (stack, "_questarr_own", lambda: "made-by-setup")):
        mock.patch.object(obj, name, value).start()
    for app_, state in ((1002, "downloading"), (1005, "searching")):
        core.db().execute("INSERT INTO game_requests(user_id, app, title, state, created, updated, next_at) VALUES (1, ?, ?, ?, ?, ?, ?)",
                          (app_, dict((a, n) for a, n, *_ in SAMPLE)[app_], state, time.time(), time.time(), time.time() + 9e9))


SHELF = [("The Lighthouse Ledger", "Mara Quill", 1987), ("Orbit of Small Things", "Tomas Ivers", 2014), ("The Clockmaker's Alibi", "Ines Marlow", 1962),
         ("A Field Guide to Rain", "Odile Brandt", 2003), ("Salt Moon", "Kenji Arroyo", 1998), ("Nine Keys to Harrow Lane", "Peter Voss", 1975),
         ("Paper Rivers", "Lena Okafor", 2019), ("The Quiet Engine", "Ravi Sandoval", 2008), ("Ink at Low Tide", "Greta Halloran", 1954),
         ("Winter Almanac", "Jonas Petrik", 1991), ("Glass Orchard", "Amara Lindqvist", 2021), ("The Last Ferry North", "Colm Raines", 1969),
         ("Letters to a Cartographer", "Sofia Benedek", 2011), ("Tin Stars", "Milo Ashby", 1983), ("The Thirteenth Lamp", "Edith Carrow", 1947),
         ("Small Hours", "Noor Valdez", 2016), ("Moth Signal", "Henrik Dale", 2000), ("A Door in the Fog", "Beatrix Lowe", 1958)]
BOOK_ROWS = ["/trending/weekly.json", "/subjects/science_fiction.json", "/subjects/mystery.json"]


def stock_book_requests():
    """Request → Books: sample books in a stand-in Open Library, one request downloading and one in the library"""
    import bookreq

    work = lambda i: {"key": f"/works/OL{i + 1}W", "title": SHELF[i][0], "author_name": [SHELF[i][1]], "cover_i": i + 1, "first_publish_year": SHELF[i][2]}  # noqa: E731
    row = lambda n: [work(i) for i in range(len(SHELF)) if i % 3 == n]  # noqa: E731
    mock.patch.object(bookreq, "ol", lambda path, **kw: {"docs": row(0)} if path == "/search.json" else {"works": row(BOOK_ROWS.index(path))}).start()
    for i, state in ((3, "downloading"), (4, "available")):
        core.db().execute("INSERT INTO book_requests(user_id, work, title, author, state, created, updated, next_at) VALUES (1, ?, ?, ?, ?, ?, ?, ?)",
                          (f"OL{i + 1}W", *SHELF[i][:2], state, time.time(), time.time(), time.time() + 9e9))


def book_cover(route):
    """a book's cover, painted here: the page asks Open Library's cover host for it"""
    i = int(re.search(r"/id/(\d+)-", route.request.url)[1]) - 1
    buf = io.BytesIO()
    art(SHELF[i][0], 400, 600, i * 5 + 2, SHELF[i][1]).save(buf, "JPEG", quality=85)
    route.fulfill(content_type="image/jpeg", body=buf.getvalue())


def game_picture(route):
    """a game's cover, painted here: the page asks Steam's image host for it"""
    appid = int(re.search(r"/apps/(\d+)/", route.request.url)[1])
    buf = io.BytesIO()
    art(dict((a, n) for a, n, *_ in SAMPLE)[appid], 600, 900, appid).save(buf, "JPEG", quality=85)
    route.fulfill(content_type="image/jpeg", body=buf.getvalue())


def save_png(data: bytes, dest: Path):
    """the PNG, as small as it goes: lossless first, then fewer colours only if it still won't fit the budget"""
    im = Image.open(io.BytesIO(data)).convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    if len(buf.getvalue()) > KB_BUDGET * 1024:
        buf = io.BytesIO()
        im.quantize(256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG).save(buf, "PNG", optimize=True)
    dest.write_bytes(buf.getvalue())
    return len(buf.getvalue())


def main():
    core.db()
    auth.create_user("maya", PASSWORD, True)
    jfs.point(core)
    stock_library()
    steam = stock_games()
    stock_live()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(env.yeerr.app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.1)
    base = f"http://127.0.0.1:{port}"
    shell = next(iter(sorted(Path.home().glob(".cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"))), None)
    errors, written = [], []

    def signed_in(b, view):
        ctx = b.new_context(color_scheme="dark", **VIEWS[view])
        p = ctx.new_page()
        p.on("pageerror", lambda e: errors.append(f"{view}: {e}"))
        p.goto(base + "/")
        p.wait_for_selector("#gateForm")
        p.fill("#gName", "maya")
        p.fill("#gPw", PASSWORD)
        p.click("#gateForm .primary")
        p.wait_for_selector("#gateForm", state="detached", timeout=30000)
        return p

    def shoot(p, name, view, route, wait, after=None, tall=None):
        """a page, dark then light; `tall` gives a desktop page more height so its shelves show"""
        if view == "desktop":
            p.set_viewport_size({"width": 1280, "height": tall or 800})
        p.goto(base + "/" + route)
        p.wait_for_selector(wait, timeout=30000)
        for scheme in ("dark", "light"):
            p.emulate_media(color_scheme=scheme)
            p.wait_for_function("[...document.images].every((i) => i.complete)", timeout=30000)
            p.wait_for_timeout(900)
            if after:
                after(p)
            p.mouse.move(0, 0)
            dest = OUT / f"{name}-{view}-{scheme}.png"
            size = save_png(p.screenshot(), dest)
            written.append((dest.name, size))
            print(f"  {dest.name}  {size // 1024} KB")

    try:
        with sync_playwright() as pw:
            b = pw.chromium.launch(executable_path=str(shell) if shell else None, args=["--autoplay-policy=no-user-gesture-required"])
            desk, phone = signed_in(b, "desktop"), signed_in(b, "phone")
            # Steam: an admin's key, then this person's profile
            desk.goto(base + "/#/games/steam")
            desk.wait_for_selector("#steamKey", timeout=20000)
            desk.fill("#steamKeyIn", KEY)
            desk.click("#steamKey .primary")
            desk.wait_for_selector("#steamLink", timeout=10000)
            desk.wait_for_timeout(800)  # let the page finish painting before typing into it
            desk.fill("#steamProfile", f"https://steamcommunity.com/profiles/{STEAM_ID}")
            with desk.expect_response(lambda r: r.url.endswith("/api/games/steam") and r.request.method == "PUT") as put:
                desk.click("#steamLink .primary")
            print("link:", put.value.status, put.value.text()[:200])
            try:
                desk.wait_for_selector("#g-steam .gshelf .gcard", timeout=15000)
            except Exception:
                print("STEAM SECTION:", desk.evaluate("fetch('/api/games/steam').then(r => r.text())")[:600], desk.inner_text("body")[-300:])
                raise
            # a TV: /tv shows a code, the phone types it
            tv = b.new_context(viewport={"width": 1280, "height": 720}, color_scheme="dark").new_page()
            tv.on("pageerror", lambda e: errors.append(f"tv: {e}"))
            tv.goto(base + "/tv")
            tv.wait_for_selector("#tvCode", timeout=30000)
            code = tv.inner_text("#tvCode").replace("\n", "").replace(" ", "")
            phone.goto(base + "/#/tvs")
            phone.wait_for_selector("#tvAdd", timeout=30000)
            phone.fill("#tvAddCode", code.lower())
            phone.fill("#tvAddName", "Living room")
            phone.click("#tvAdd .primary")
            tv.wait_for_function("document.body.classList.contains('tv') && tvMe && tvMe.name === 'Living room'", timeout=30000)
            tv.evaluate("location.hash = '#/jf'")
            tv.wait_for_selector(".jcard", timeout=30000)
            tv.wait_for_function("[...document.images].every((i) => i.complete)", timeout=30000)
            tv.wait_for_timeout(1200)
            for scheme in ("dark", "light"):
                tv.emulate_media(color_scheme=scheme)
                tv.wait_for_timeout(500)
                dest = OUT / f"tv-screen-desktop-{scheme}.png"
                written.append((dest.name, save_png(tv.screenshot(), dest)))
                print(f"  {dest.name}  {written[-1][1] // 1024} KB")
            # Live: a Twitch channel followed, a YouTube live in the player
            for login, *_ in LIVE + [("napper",)]:
                desk.evaluate(f"fetch('/api/live/tw/follows', {{method: 'POST', headers: {{'Content-Type': 'application/json', 'X-Requested-With': 'parlor'}}, body: JSON.stringify({{login: '{login}'}})}})")
            shoot(desk, "live", "desktop", "#/live", ".grid .tcard:nth-child(4)", tall=1000)
            shoot(desk, "twitch", "desktop", "#/live/tw/speedy", "#chat li:nth-child(6)", lambda q: q.wait_for_function(
                "(() => { const v = document.querySelector('#video'); return v && v.currentTime > 1; })()", timeout=30000))
            shoot(desk, "games", "desktop", "#/games", "#g-retro .gcard.retro", tall=1300)
            # a YouTube live in Parlor's player, with its own picture (the YouTube and Twitch addresses differ, so nothing is cached across)
            picture = live_standin.SEG / "radio.png"
            art("Rainy night radio", 640, 360, 4).save(picture)
            live_standin.make_segments("live", ["-loop", "1", "-i", str(picture)])
            shoot(desk, "watch-live", "desktop", "#/watch/abcdefghijk", ".livebadge", lambda q: q.wait_for_function(
                "(() => { const v = document.querySelector('#video'); return v && v.currentTime > 1; })()", timeout=30000))
            for view, p in (("desktop", desk), ("phone", phone)):
                shoot(p, "library", view, "#/jf", ".jcard", tall=1240)
                shoot(p, "film", view, f"#/jf/item/{jfs.FILM}", ".hero .primary")
            # the remote, with a film playing on the TV
            phone.goto(base + f"/#/jf/item/{jfs.FILM}")
            phone.wait_for_selector("[data-cast-jf]", timeout=30000)
            phone.click("[data-cast-jf]")
            tv.wait_for_function("document.querySelector('#jfVideo') && !document.querySelector('#jfVideo').paused", timeout=30000)
            phone.set_viewport_size({"width": 390, "height": 866})  # a little taller: the volume row shows whole
            tid = phone.evaluate("fetch('/api/tv').then(r => r.json()).then(r => r.tvs[0].id)")
            shoot(phone, "remote", "phone", f"#/tvs/{tid}", "#rStick", lambda q: (q.wait_for_function(
                "document.querySelector('#rStatus')?.textContent.startsWith('On') && !document.querySelector('#toast.show') && document.querySelector('#rTitle')?.textContent === 'Night Train'", timeout=30000),
                q.wait_for_timeout(600)))  # the toast fades out in 0.18 s: don't shoot it half gone
            # Request → Games, with Steam's store and Questarr standing in
            stock_game_requests()
            desk.context.route("https://shared.akamai.steamstatic.com/**", game_picture)
            shoot(desk, "game-requests", "desktop", "#/gamereq", ".pstrip .pcard[data-game] img", tall=1100)
            # Request → Books, with Open Library standing in
            stock_book_requests()
            desk.context.route("https://covers.openlibrary.org/**", book_cover)
            shoot(desk, "book-requests", "desktop", "#/bookreq", ".pstrip .pcard[data-book] img", tall=1100)
            # Films & shows → Collections, with Seerr standing in from here on
            stock_sets()
            desk.context.route("https://image.tmdb.org/**", set_picture)
            shoot(desk, "collections", "desktop", "#/sets", ".logogrid + .shelf-head + .logogrid img", tall=1200)
            shoot(desk, "request-all", "desktop", "#/set/collection/100", "#setGrid .pcard", lambda q: (
                q.locator("#setAsk[open]").count() or q.click("#askAll"), q.wait_for_selector("#setAsk[open]"), q.wait_for_timeout(600)), tall=900)
            # Request: everything you can ask for on one page
            shoot(desk, "request", "desktop", "#/request", "#ask-books .pcard[data-book] img", lambda q: q.wait_for_selector("#ask-films .pcard img"), tall=1180)
            b.close()
    finally:
        server.should_exit = True
        steam.shutdown()
        jfs.JF.shutdown()
    print("page errors:", errors or "none")
    big = [w for w in written if w[1] > KB_BUDGET * 1024]
    print(f"{len(written)} screenshots in {OUT}; over budget: {big or 'none'}")
    pages = [ROOT / "README.md", *(ROOT / "docs").glob("*.md")]  # the docs pages name them as assets/screens/…
    used = {name for page in pages for name in re.findall(r"assets/screens/([\w-]+\.png)", page.read_text())}
    made = {name for name, _ in written}
    print(f"the README and docs show {len(used)}; not made: {sorted(used - made) or 'none'}; made but not shown: {sorted(made - used) or 'none'}")
    ok = made == used and not big and not errors
    print("RESULT:", "ok" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
