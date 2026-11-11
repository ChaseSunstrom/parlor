"""Books you ask for, inside Parlor (Request → Books). Browsing and the search are answered from Open Library (no key;
a book's id is its work there, OL…W). A request is a row here, and Parlor does the rest itself, there being no app
for books in the stack: a worker searches Prowlarr's indexers for the book, hands the one release that fits to
qBittorrent (behind the VPN like everything else), and when it has finished links the book files, and only those,
into the Books library.

A download comes from strangers, so:
- only a release whose name is the book's title and its author's, and little else, is taken (fits);
- Prowlarr's links carry its key: Parlor fetches the magnet or the .torrent itself, follows a link nowhere but to
  Prowlarr, and no link reaches a page, the log or a reason shown;
- only book files are brought in, under names Parlor makes, and nothing in a download is ever run."""
import json
import logging
import os
import re
import shutil
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import requests
from fastapi import APIRouter, Depends, HTTPException

import arr
import jf
import qbit
import stack
from core import db, me, one, rows
from gamereq import clean
from yt import Cache

log = logging.getLogger("parlor.bookreq")
router = APIRouter(prefix="/api/books/requests")
DOWNLOADS = Path(os.environ.get("BOOK_DOWNLOADS", "/data/downloads/books"))  # the same path in qBittorrent's container
BOOKS = Path(os.environ.get("BOOKS_DIR", "/data/books"))  # Jellyfin's Books library
EVERY, LOOK_AGAIN, MAX_ERRORS, GIVE_UP = 300, 6 * 3600, 12, 12 * 3600
MOST, PER_HOUR = 10, 20  # open requests a member may have at once, and new ones in an hour (each searches every indexer)
TORRENT_FILE = 5 * 2 ** 20  # no .torrent of a book is bigger
MAX_SIZE, MAX_EXTRA, MIN_SEEDERS, PER_LOOK, MOST_FILES = 300 * 2 ** 20, 3, 1, 4, 10
FORMATS = (".epub", ".pdf", ".mobi", ".azw3", ".cbz", ".cbr")  # best first: Parlor's reader opens EPUB
NOISE = {"by", "epub", "mobi", "pdf", "azw3", "azw", "cbz", "cbr", "ebook", "ebooks", "retail", "kindle", "book", "novel", "en", "eng", "english"}
OPEN = ("searching", "downloading")
LABEL = {"searching": "Searching", "downloading": "Downloading", "available": "In your library", "failed": "Stuck"}
FIELDS = "key,title,author_name,cover_i,first_publish_year,edition_count,ratings_average"
SHELVES = {"trending": "/trending/weekly.json", "scifi": "/subjects/science_fiction.json", "mystery": "/subjects/mystery.json"}
_tick = threading.Lock()  # one check at a time; the pages never wait for it
_cache = Cache(3 * 3600, 400)  # Open Library asks to be asked gently
_asked = {}  # a member's id: when they asked lately. ponytail: in memory, so a restart forgets; a table if that's ever leaned on
wake = threading.Event()


# --- the catalogue: Open Library ---------------------------------------------------------------------

def ol(path, **params):
    """One page of Open Library's, as JSON. No key, and nothing about this server or its people goes along."""
    try:
        r = requests.request("GET", "https://openlibrary.org" + path, params=params, headers={"User-Agent": "Parlor (a self-hosted media app)"}, timeout=20)
        if r.status_code == 404:
            raise HTTPException(404, "Open Library has no such page")
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError):
        raise HTTPException(502, "Open Library didn't answer")


def kept(path, **params):
    key = (path, *sorted(params.items()))
    return _cache.get(key) or _cache.put(key, ol(path, **params))


def asked() -> dict:
    return {r["work"]: r["state"] for r in rows("SELECT work, state FROM book_requests")}


def card(w: dict, have: dict) -> dict:
    """A work as a search lists it, or as a subject does (authors and cover under other names)."""
    work, cover = w["key"].rsplit("/", 1)[-1], w.get("cover_i") or w.get("cover_id")
    author = (w.get("author_name") or [a.get("name") for a in w.get("authors") or []] or [""])[0]
    return {"id": work, "title": str(w.get("title") or "Untitled")[:300], "author": str(author or "")[:200],
            "poster": f"https://covers.openlibrary.org/b/id/{int(cover)}-M.jpg" if cover else None, "year": w.get("first_publish_year"), "status": have.get(work)}


def cards(works) -> list:
    have = asked()
    return [card(w, have) for w in works or [] if isinstance(w, dict) and re.fullmatch(r"/works/OL\d+W", str(w.get("key")))]


def found(work: str) -> dict:
    docs = kept("/search.json", q=f"key:/works/{work}", fields=FIELDS, limit=1)["docs"] if re.fullmatch(r"OL\d{1,12}W", work) else []
    if not docs:
        raise HTTPException(404, "No such book at Open Library")
    return docs[0]


@router.get("/discover")
def discover(_=Depends(me)):
    def shelf(path):
        try:
            return kept(path, limit=24)["works"]
        except HTTPException:
            return None  # one row not answering leaves the others
    with ThreadPoolExecutor(len(SHELVES)) as pool:
        got = dict(zip(SHELVES, pool.map(shelf, SHELVES.values())))
    if all(v is None for v in got.values()):
        raise HTTPException(502, "Open Library didn't answer")
    return {name: cards(works) for name, works in got.items()}


@router.get("/search")
def search(q: str = "", _=Depends(me)):
    q = " ".join(q.split())[:100]
    return {"items": cards(kept("/search.json", q=q, fields=FIELDS, limit=40)["docs"]) if q else []}


@router.get("/book/{work}")
def book(work: str, user=Depends(me)):
    w = found(work)
    try:
        more = kept(f"/works/{work}.json")
    except HTTPException:
        more = {}  # the card is enough to ask by
    about = more.get("description") or ""
    about = about.get("value", "") if isinstance(about, dict) else str(about)
    r = one(ROW + " WHERE b.work = ?", work)
    return {**card(w, {work: r["state"]} if r else {}), "summary": re.split(r"\n-{5,}|\n\[1\]:|\(\[source", about)[0].strip()[:3000],  # its footnotes aren't the blurb
            "subjects": [str(s)[:60] for s in (more.get("subjects") or [])[:8]], "rating": round(w["ratings_average"], 1) if w.get("ratings_average") else None,
            "editions": w.get("edition_count"), "store": f"https://openlibrary.org/works/{work}", "request": view(r, user) if r else None}


# --- asking, and the list ----------------------------------------------------------------------------

ROW = "SELECT b.*, u.name AS asker FROM book_requests b LEFT JOIN users u ON u.id = b.user_id"


def view(r: dict, user: dict) -> dict:
    mine = r["user_id"] == user["id"]
    theirs = mine or user["admin"]  # anyone else learns that it's asked for and where it's at, not by whom or why
    return {"id": r["id"], "work": r["work"], "title": r["title"], "author": r["author"], "image": r["image"], "year": r["year"], "state": r["state"],
            "label": LABEL.get(r["state"], r["state"]), "why": r["why"] if theirs else "", "by": r.get("asker") if theirs else None, "mine": mine,
            "created": r["created"], "updated": r["updated"], "release": r["release"] if theirs else None}


def moved(rid, was: tuple, **values) -> bool:
    """Move a request on, only if it is still in one of these states: a tap and the worker, or two taps, act once."""
    values.setdefault("updated", time.time())
    if values.get("why"):
        values["why"] = clean(values["why"])
    cur = db().execute(f"UPDATE book_requests SET {', '.join(f'{k} = ?' for k in values)} WHERE id = ? AND state IN ({','.join('?' * len(was))})",
                       (*values.values(), rid, *was))
    return cur.rowcount == 1


def row(rid, user, anyone=False) -> dict:
    r = one(ROW + " WHERE b.id = ?", rid)
    if not r or not (anyone or user["admin"] or r["user_id"] == user["id"]):
        raise HTTPException(404, "No such request")
    return r


def fresh(rid, user) -> dict:
    wake.set()
    return view(row(rid, user, anyone=True), user)


@router.get("")
def listing(user=Depends(me)):
    q = ROW + ("" if user["admin"] else " WHERE b.user_id = ?") + " ORDER BY b.updated DESC LIMIT 300"
    return {"items": [view(r, user) for r in rows(q, *(() if user["admin"] else (user["id"],)))]}


@router.post("/book/{work}")
def ask(work: str, user=Depends(me)):
    """No admin's yes, unlike a game: a book is read, not run, and only book files reach the library."""
    b = card(found(work), {})
    had = one("SELECT id, state FROM book_requests WHERE work = ?", work)
    if had and had["state"] != "failed":
        return fresh(had["id"], user)  # one request per book: whoever asked first
    if not user["admin"] and one(f"SELECT COUNT(*) AS n FROM book_requests WHERE user_id = ? AND state IN ({','.join('?' * len(OPEN))})", user["id"], *OPEN)["n"] >= MOST:
        raise HTTPException(429, f"You have {MOST} books on the way: ask again when one has arrived")
    now = time.time()
    if not user["admin"]:  # asking and cancelling in a loop would search the indexers each time round
        lately = _asked[user["id"]] = [t for t in _asked.get(user["id"], []) if now - t < 3600]
        if len(lately) >= PER_HOUR:
            raise HTTPException(429, f"That's {PER_HOUR} books asked for this hour: the indexers need a rest. Ask again later")
        lately.append(now)
    if had:  # a stuck one, asked for again: the same row, so what was tried isn't taken twice
        moved(had["id"], ("failed",), state="searching", user_id=user["id"], why="", release=None, tries=0, gone=0, next_at=0)
        return fresh(had["id"], user)
    cur = db().execute("INSERT OR IGNORE INTO book_requests(user_id, work, title, author, image, year, state, created, updated) VALUES (?, ?, ?, ?, ?, ?, 'searching', ?, ?)",
                       (user["id"], work, b["title"], b["author"], b["poster"], b["year"], now, now))
    return fresh(cur.lastrowid if cur.rowcount else one("SELECT id FROM book_requests WHERE work = ?", work)["id"], user)  # two people at once: the first one's


@router.post("/{rid}/retry")
def retry(rid: int, user=Depends(me)):
    """Search again; a download that's under way is dropped first, and that release isn't taken again."""
    r = row(rid, user)
    if not user["admin"] and time.time() - r["updated"] < EVERY:  # each search asks every indexer, and they ban the eager
        raise HTTPException(429, "It was looked for a moment ago: try again in a few minutes")
    if not again(r):
        raise HTTPException(409, "It's in your library already")
    return fresh(rid, user)


@router.delete("/{rid}")
def cancel(rid: int, user=Depends(me)):
    """The request and its download go; a book already brought into the library stays there."""
    r = row(rid, user)
    drop(r)  # qBittorrent not answering: the request stays listed, to cancel later
    db().execute("DELETE FROM book_requests WHERE id = ?", (r["id"],))
    return {"ok": True}


# --- which release -----------------------------------------------------------------------------------

def flat(text) -> str:
    """Lower case, no accents, no apostrophes; dots and underscores are spaces, as release names use them."""
    plain = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[._]", " ", plain.replace("'", ""))


def words(text) -> list:
    return re.findall(r"[a-z0-9]+", flat(text))


def main(title: str) -> str:
    """A title without its subtitle: 'Frankenstein; or, The Modern Prometheus' is posted as 'Frankenstein'."""
    return re.split(r"[:;(\[]| - ", title)[0].strip() or title


def surname(author: str) -> str:
    """The author's last name, as written: what the indexers are searched by."""
    return next((w for w in reversed(re.findall(r"[^\W\d_]+", unicodedata.normalize("NFKD", author).encode("ascii", "ignore").decode()))
                 if w.lower() not in ("jr", "sr", "ii", "iii")), "")


def surnames(author: str) -> set:
    """The ways a release may write it: L'Engle is 'lengle' in one name and 'l engle' in the next."""
    last = next((w for w in reversed(str(author).split()) if w.lower().strip(".") not in ("jr", "sr", "ii", "iii")), "")
    return {w for w in ("".join(words(last)), surname(author).lower()) if w}


def magnet(v) -> bool:
    """A magnet link and nothing else: qBittorrent takes a line per link, so a second line would be a second download from anywhere."""
    return isinstance(v, str) and bool(re.fullmatch(r"magnet:\?[!-~]{1,4000}", v))


def link(h: dict) -> str:
    for k in ("magnetUrl", "downloadUrl", "guid"):
        v = h.get(k)
        if magnet(v) or (k != "guid" and isinstance(v, str) and re.fullmatch(r"https?://[!-~]{1,4000}", v)):
            return v
    return ""


def fits(h, title: str, author: str):
    """How well a search result fits the book (less is better), or None when it isn't to be taken: a torrent of a
    book's size that people are seeding, whose name has the title standing on its own (not inside a longer one: 'Dune
    Messiah', 'The Road to Dune'), the author's surname, and at most a few words besides (an edition's name passes, a
    collection doesn't).
    ponytail: words in a name, which a stranger chose. It can take another edition, a translation or a fake; only book
    files reach the library, and Search again takes the next one. An app that knows editions (LazyLibrarian, Readarr's
    successors) is the upgrade."""
    if not (isinstance(h, dict) and isinstance(h.get("title"), str) and h.get("protocol") == "torrent" and link(h)):
        return None
    seeders, size = h.get("seeders"), h.get("size")
    if not (isinstance(seeders, int) and seeders >= MIN_SEEDERS and isinstance(size, int) and 0 < size <= MAX_SIZE):
        return None
    if any(3000 <= (c.get("id") or 0) < 4000 for c in h.get("categories") or [] if isinstance(c, dict)):  # audio: an audiobook
        return None
    name, got, last, wanted = flat(h["title"]), words(h["title"]), surnames(author), words(main(title))
    if not wanted:  # a title in another script: there's nothing here to match it by
        return None
    writer = set(words(author)) | last
    beside = writer | NOISE
    edge = lambda w: w in beside or re.fullmatch(r"(1[5-9]|20)\d\d", w)  # noqa: E731  (what may stand next to the title: the author, a format, a year)
    for m in re.finditer(r"(?<![a-z0-9])" + r"[^a-z0-9]+".join(wanted) + r"(?![a-z0-9])", name):
        before, after = name[:m.start()].rstrip(), name[m.end():].lstrip()
        if (not before or not before[-1].isalnum() or edge(re.search(r"[a-z0-9]+$", before)[0])) and (not after or not after[0].isalnum() or edge(re.match(r"[a-z0-9]+", after)[0])):
            break
    else:
        return None
    if last and not last & set(got):
        return None
    known = set(words(title)) | beside
    extra = sum(1 for w in got if w not in known and not re.fullmatch(r"(1[5-9]|20)\d\d", w))
    if extra > (MAX_EXTRA if last else 0):  # no author to go by: the title and nothing more
        return None
    named = [f for f in FORMATS if f[1:] in got]
    return (0 if ".epub" in named else 2 if named else 1, extra, -seeders)


def ranked(hits, title: str, author: str, tried=()) -> list:
    scored = [(fits(h, title, author), i, h) for i, h in enumerate(hits if isinstance(hits, list) else [])]
    return [h for score, _, h in sorted((s for s in scored if s[0] is not None), key=lambda s: s[:2]) if h["title"] not in tried]


def best(hits, title: str, author: str, tried=()) -> Optional[dict]:
    return next(iter(ranked(hits, title, author, tried)), None)


# --- qBittorrent ---------------------------------------------------------------------------------------

def tag(r: dict) -> str:
    return f"parlor-book-{r['id']}"


def torrent(r: dict) -> Optional[dict]:
    got = qbit.ok(qbit.api("GET", "/torrents/info", params={"tag": tag(r)})).json()
    return got[0] if got else None


def drop(r: dict):
    """The request's download, and what it downloaded, out of qBittorrent."""
    t = torrent(r)
    if t:
        qbit.ok(qbit.api("POST", "/torrents/delete", data={"hashes": t["hash"], "deleteFiles": "true"}))


def fetch(url: str) -> dict:
    """What qBittorrent is to be given for a release: its magnet, or the .torrent itself. qBittorrent sits behind the
    VPN and may not reach Prowlarr, so Parlor fetches it; a link is followed only while it stays at Prowlarr (a
    release's link is a stranger's: it could point at anything on this network). ValueError: not this one."""
    home = arr.url("prowlarr") + "/"
    for _ in range(4):
        if magnet(url):
            return {"data": {"urls": url}}
        if not url.startswith(home):
            raise ValueError("its link leads away from Prowlarr")
        try:
            r = requests.get(url, allow_redirects=False, timeout=60, stream=True)  # read no further than a .torrent's size
            body = r.raw.read(TORRENT_FILE + 1, decode_content=True) if r.status_code == 200 else b""
            r.close()
        except requests.RequestException as exc:  # never the error's own words: they have the link in them, and it carries Prowlarr's key
            raise HTTPException(502, f"Prowlarr didn't hand over the download ({type(exc).__name__})") from None
        if r.status_code in (301, 302, 303, 307, 308):
            url = r.headers.get("Location") or ""
        elif body[:1] == b"d" and len(body) <= TORRENT_FILE:
            return {"data": {}, "files": {"torrents": ("book.torrent", body, "application/x-bittorrent")}}
        else:
            raise ValueError(f"Prowlarr said {r.status_code} for it, or it isn't a torrent")
    raise ValueError("its link goes round in circles")


def add(r: dict, h: dict):
    """The release into qBittorrent, in its own folder and category, tagged with the request."""
    got = fetch(link(h))
    said = qbit.ok(qbit.api("POST", "/torrents/add", data={**got["data"], "category": "books", "savepath": str(DOWNLOADS), "tags": tag(r)}, files=got.get("files"))).text.strip()
    if qbit.refused(said):
        raise ValueError("qBittorrent didn't take it: it's already there, or it isn't a torrent")


def again(r: dict) -> bool:
    """Back to searching, without the release it had (the download is dropped; that release isn't taken again)."""
    if r["state"] == "available":
        return False
    drop(r)
    tried = json.loads(r["tried"] or "[]") + ([r["release"]] if r["release"] else [])
    return moved(r["id"], ("searching", "downloading", "failed"), state="searching", why="", release=None, tried=json.dumps(tried[-50:]), tries=0, gone=0, next_at=0)


# --- into the library --------------------------------------------------------------------------------

def safe(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", name)).strip(" .")[:120].strip(" .") or "Unknown"


def bring(r: dict, t: dict) -> int:
    """Link the download's book files into Books/<Author>/<Title>/, and say how many: one format (the best there),
    regular files only, from inside the downloads folder only, under names made here. Nothing else leaves the folder."""
    root, src = DOWNLOADS.resolve(), Path(t.get("content_path") or DOWNLOADS).resolve()
    if root not in src.parents:
        return 0
    files = [src] if src.is_file() else sorted(Path(d) / f for d, _, names in os.walk(src) for f in names)
    files = [f for f in files if f.suffix.lower() in FORMATS and f.is_file() and not f.is_symlink()]
    if not files:
        return 0
    ext = min((f.suffix.lower() for f in files), key=FORMATS.index)
    files = [f for f in files if f.suffix.lower() == ext][:MOST_FILES]
    title = safe(r["title"])
    folder = BOOKS / safe(r["author"] or "Unknown author") / title
    folder.mkdir(parents=True, exist_ok=True)
    made = [folder.parent, folder]
    for i, f in enumerate(files):
        to = folder / f"{title}{f' {i + 1}' if i else ''}{ext}"
        if to.exists() or to.is_symlink():
            continue  # already on the shelf, perhaps put there by hand: never replaced
        try:
            os.link(f, to)  # no second copy on the disk while it seeds
        except OSError:  # the library is on another disk
            shutil.copy2(f, to)
        made.append(to)
    stack._own(*made)  # Parlor writes as root; the library belongs to the apps' user
    return len(files)


def rescan():
    try:
        jf.fetch("POST", "/Library/Refresh")
    except HTTPException:
        pass  # Jellyfin's own scan finds the book later


# --- the worker --------------------------------------------------------------------------------------

def look(r: dict, now: float):
    """Search the indexers for the book and start the release that fits best; none: later."""
    q = f"{main(r['title'])} {surname(r['author'])}".strip()
    try:
        hits = arr.call("prowlarr", "GET", "/search", timeout=120, params={"query": q[:200], "categories": [7000, 7020], "type": "search", "limit": 100})
    except arr.ArrError as exc:
        log.warning("book requests: %s", exc)
        raise HTTPException(502, "The indexers couldn't be searched") from None  # the address stays in the log
    picks = ranked(hits, r["title"], r["author"], json.loads(r["tried"] or "[]"))[:PER_LOOK]
    here = ("searching",)  # the search took a while: only a request that's still searching moves on (it may be cancelled)
    if picks and not moved(r["id"], here, state="downloading"):
        return
    try:
        for h in picks:
            try:
                add(r, h)
            except ValueError as exc:
                log.info("book requests: not %r: %s", h["title"][:100], exc)
                continue
            if not moved(r["id"], ("downloading",), release=h["title"][:300], why="", gone=0, next_at=now + 60):
                drop(r)  # cancelled while it was being added
            return
    except Exception:
        moved(r["id"], ("downloading",), state="searching", updated=r["updated"])
        raise
    moved(r["id"], OPEN, state="searching", next_at=now + LOOK_AGAIN, updated=r["updated"],
          why=f"Nothing that fits on the indexers yet; looking again in {LOOK_AGAIN // 3600} hours")


def follow(r: dict, now: float):
    """A download: still going, gone, or done and brought into the library."""
    t = torrent(r)
    here = ("downloading",)  # qBittorrent took a moment: only a request that's still downloading moves on (Try another may have come)
    if not t:
        if r["gone"]:  # twice, minutes apart: not just qBittorrent being slow to list it
            return moved(r["id"], here, state="failed", why="qBittorrent no longer has the download: it was removed there. Search again fetches it anew")
        return moved(r["id"], here, gone=1, next_at=now + EVERY, updated=r["updated"])
    big = isinstance(t.get("size"), int) and t["size"] > MAX_SIZE  # what the torrent really holds; the indexer's word was all there was before
    if big or t.get("progress", 0) < 1 or str(t.get("state", "")).startswith(("checking", "moving")):
        if big or time.time() - r["updated"] > GIVE_UP:
            again(r)
            return moved(r["id"], ("searching",), updated=r["updated"],
                         why="That download was far bigger than a book; looking for another" if big else "That download wasn't getting anywhere; looking for another")
        return moved(r["id"], here, gone=0, next_at=now + 30, updated=r["updated"])
    if bring(r, t):
        if moved(r["id"], here, state="available", why=""):
            rescan()
        return
    try:
        drop(r)  # whatever it was, it doesn't stay on the disk
    except HTTPException:
        log.warning("book requests: a download with no book in it is still in qBittorrent (tag %s)", tag(r))
    tried = json.loads(r["tried"] or "[]") + [r["release"]]
    moved(r["id"], here, state="failed", tried=json.dumps(tried[-50:]), release=None, why="That download had no book in it. Search again looks for another")


def tick(now: Optional[float] = None):
    """Every open request that's due, one step each; trouble means later, with a longer wait each time."""
    now = now or time.time()
    with _tick:
        for rid in [r["id"] for r in rows(f"SELECT id FROM book_requests WHERE state IN ({','.join('?' * len(OPEN))}) AND next_at <= ? ORDER BY id", *OPEN, now)]:
            r = one("SELECT * FROM book_requests WHERE id = ?", rid)  # as it is now: the page may have just moved it
            if not r or r["state"] not in OPEN:
                continue
            try:
                if r["tries"]:  # the trouble's words go with the trouble; this step writes its own if it has any
                    db().execute("UPDATE book_requests SET tries = 0, why = '' WHERE id = ?", (rid,))
                (look if r["state"] == "searching" else follow)(r, now)
            except Exception as exc:  # noqa: BLE001  (one stuck request never stops the others)
                tries, said = r["tries"] + 1, str(getattr(exc, "detail", None) or type(exc).__name__)[:120]
                if tries >= MAX_ERRORS:
                    moved(rid, OPEN, state="failed", tries=tries, why=f"Gave up after {tries} errors in a row: {said}")
                    continue
                wait = min(EVERY * 2 ** tries, 6 * 3600)
                moved(rid, OPEN, tries=tries, next_at=now + wait, updated=r["updated"], why=f"{said}; trying again in {wait // 60} min")


def start():
    def loop():
        while True:
            try:
                tick()
            except Exception:  # noqa: BLE001
                log.exception("book requests: the check failed")
            wake.wait(30 if one("SELECT 1 AS x FROM book_requests WHERE state = 'downloading'") else EVERY)  # a book is minutes at most
            wake.clear()
    threading.Thread(target=loop, daemon=True, name="book-requests").start()
