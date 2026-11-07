"""An index of the music folder's tags in SQLite (the `tracks` table), so the Subsonic API answers from a query
instead of opening every file. Only new or changed files are read again. Ids are hashes of the relative path
(tracks), or of album artist + album and of album artist (albums, artists), so they survive a re-index."""

import hashlib
import logging
import os
import threading
import time
from pathlib import Path

from mutagen import File as AudioFile

import core

log = logging.getLogger("yeerr.library")
AUDIO = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".wav"}  # the same as music-fndr's
EVERY = 10 * 60
_lock = threading.Lock()  # one scan at a time
status = {"scanning": False, "last": 0.0}


def root() -> Path:
    return Path.cwd()  # music-fndr's folder: the app runs from it


def hid(prefix, *parts) -> str:
    return f"{prefix}-" + hashlib.sha1("\0".join(parts).encode()).hexdigest()[:16]


def _num(value):
    try:
        return int(str(value).split("/")[0])
    except (TypeError, ValueError):
        return None


def read(path: Path, rel: str, st) -> dict:
    """One row for the index; untagged or unreadable files still get one, named after the file and folder."""
    tags, info = {}, None
    try:
        audio = AudioFile(path, easy=True)
        tags, info = (audio.tags or {}), audio.info
    except Exception:  # not really audio, or no tag support (wav)
        try:
            info = AudioFile(path).info
        except Exception:
            pass
    first = lambda k: next((str(v).strip() for v in (tags.get(k) or []) if str(v).strip()), "")  # noqa: E731
    artists = [str(v).strip() for v in tags.get("artist") or [] if str(v).strip()]
    folder = Path(rel).parent.name
    title, album = first("title") or Path(rel).stem, first("album") or folder or "Unknown Album"
    album_artist = first("albumartist") or (artists[0] if artists else "Unknown Artist")
    year = _num(first("date")[:4])
    return {"id": hid("tr", rel), "path": rel, "title": title, "artist": ", ".join(artists) or album_artist,
            "album": album, "album_artist": album_artist, "album_id": hid("al", album_artist.casefold(), album.casefold()),
            "artist_id": hid("ar", album_artist.casefold()), "track": _num(first("tracknumber")),
            "disc": _num(first("discnumber")), "year": year, "genre": first("genre") or None,
            "secs": round(getattr(info, "length", 0) or 0, 2), "bitrate": (getattr(info, "bitrate", 0) or 0) // 1000,
            "size": st.st_size, "mtime": st.st_mtime, "suffix": Path(rel).suffix.lower().lstrip(".")}


def refresh() -> int:
    """Index what changed since last time; returns how many tracks there are. Hidden and NAS system
    folders (.music-findr, @eaDir) are skipped. A second call while one runs waits for it."""
    with _lock:
        status["scanning"] = True
        try:
            return _refresh()
        finally:
            status.update(scanning=False, last=time.time())


def _refresh():
    top = root()
    known = {r["path"]: (r["mtime"], r["size"]) for r in core.rows("SELECT path, mtime, size FROM tracks")}
    found, batch, insert = set(), [], None

    def save():
        nonlocal insert
        if batch:
            insert = insert or "INSERT OR REPLACE INTO tracks(%s) VALUES (%s)" % (
                ",".join(batch[0]), ",".join("?" * len(batch[0])))
            conn = core.db()
            conn.execute("BEGIN")
            conn.executemany(insert, [tuple(r.values()) for r in batch])
            conn.execute("COMMIT")
            batch.clear()

    for here, dirs, files in os.walk(top):
        dirs[:] = [d for d in dirs if not d.startswith((".", "@"))]
        for name in files:
            if Path(name).suffix.lower() not in AUDIO or name.startswith("."):
                continue
            path = Path(here, name)
            rel = path.relative_to(top).as_posix()
            try:
                st = path.stat()
            except OSError:
                continue
            found.add(rel)
            if known.get(rel) != (st.st_mtime, st.st_size):
                batch.append(read(path, rel, st))
                if len(batch) >= 200:  # the library fills in as the first scan goes
                    save()
    save()
    gone = [(p,) for p in known if p not in found]
    if gone:
        core.db().executemany("DELETE FROM tracks WHERE path = ?", gone)
    return len(found)


_job = None


def wait():
    """Block until a scan started in the background is over (tests)."""
    if _job:
        _job.join()


def scan_in_background():
    global _job
    if not (_job and _job.is_alive()):
        status["scanning"] = True  # visible at once, before the thread gets going
        _job = threading.Thread(target=_safely, daemon=True, name="library-scan")
        _job.start()


def _safely():
    try:
        refresh()
    except Exception:
        log.exception("library scan failed")


def start():
    """Scan now and every ten minutes (called once at startup)."""
    def loop():
        while True:
            _safely()
            time.sleep(EVERY)

    threading.Thread(target=loop, daemon=True, name="library-index").start()
