"""A Jellyfin stand-in for the browser tests: a small HTTP server speaking the Jellyfin routes Parlor uses, serving real
media made with ffmpeg (a film, a show with two episodes, an album of two songs, an EPUB) with byte ranges. Importing
it makes the media and starts the server at JF; point Parlor at it with point(core)."""
import json
import re
import socket
import subprocess
import tempfile
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

MEDIA = Path(tempfile.mkdtemp(prefix="jf-media-"))
KEY = "STANDINKEY0123"  # pragma: allowlist secret (made up)
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
DOWNLOADS: list = []  # (item, Range) for each original file asked for
RANGES: list = []  # (file name, Range header) of every file asked for
BREAK: set = set()  # file names whose next request for a later piece fails once (a dropped connection)
NODIRECT: set = set()  # item ids Jellyfin would have to convert (no direct play)
DOWN = {"on": False, "hits": 0}  # while on, Jellyfin answers 500 to every place saved (hits counts them)
VTT = "WEBVTT\n\n00:00:00.000 --> 00:00:11.000\nThe night train never stops\n"  # the film's external subtitle file


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
        RANGES.append((path.name, self.headers.get("Range")))
        rng = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
        if rng and int(rng[1] or 0) > 0 and path.name in BREAK:
            BREAK.discard(path.name)
            return self.send(503, {})
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
            if m[2] == "Download":  # the original file, as it is
                DOWNLOADS.append((m[1], self.headers.get("Range")))
                return self.file(MEDIA / i["_file"], "application/epub+zip" if i["_file"].endswith(".epub") else "video/mp4")
            kind = "wide.png" if "Backdrop" in m[2] or i["Type"] == "Episode" else "square.png" if i["Type"] in ("MusicAlbum", "Audio", "MusicArtist") else "poster.png"
            return self.file(MEDIA / kind, "image/png")
        if re.fullmatch(rf"/Videos/\w+/{MS}/Subtitles/\d+/Stream\.vtt", p):
            return self.send(200, body=VTT.encode(), ctype="text/vtt")
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
            if m[1] == FILM:  # one text subtitle Jellyfin serves as a file, one picture-based one it can't
                streams += [{"Index": 2, "Type": "Subtitle", "Codec": "webvtt", "Language": "eng", "DisplayTitle": "English", "DeliveryMethod": "External",
                             "DeliveryUrl": f"/Videos/{FILM}/{MS}/Subtitles/2/Stream.vtt"},
                            {"Index": 3, "Type": "Subtitle", "Codec": "pgssub", "Language": "fre", "DisplayTitle": "French (PGS)", "DeliveryMethod": "Embed"}]
            src = {"Id": MS, "Container": "mp3" if audio else "mp4", "SupportsDirectPlay": m[1] not in NODIRECT, "RunTimeTicks": i["RunTimeTicks"], "MediaStreams": streams}
            if m[1] in NODIRECT:
                src["TranscodingUrl"] = f"/videos/{m[1]}/master.m3u8?MediaSourceId={MS}&PlaySessionId=ps"
            return self.send(200, {"PlaySessionId": "ps", "MediaSources": [src]})
        m = re.fullmatch(r"/UserItems/(\w+)/UserData", p)
        if m and m[1] in ITEMS:
            if DOWN["on"]:
                DOWN["hits"] += 1
                return self.send(500, {})
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


def point(core):
    """Parlor's settings for this Jellyfin"""
    for k, v in (("jellyfin_url", f"http://127.0.0.1:{JF.server_address[1]}"), ("jellyfin_key", KEY), ("jellyfin_user", "family")):
        core.set_setting(k, v)
