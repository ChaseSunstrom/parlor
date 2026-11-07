"""Shared bits: the SQLite database, settings, accounts and sessions."""

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from fastapi import HTTPException, Request

DATA = Path(os.environ.get("DATA_DIR", "/data"))
VIDEOS = Path(os.environ.get("VIDEO_DIR", "/videos"))
SESSION_DAYS = 30
COOKIE = "yeerr_session"
HEADER = "yeerr"  # X-Requested-With value every change must carry (other sites can't send it)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  pw TEXT,                      -- scrypt hash; NULL for Jellyfin accounts
  admin INTEGER NOT NULL DEFAULT 0,
  jellyfin_id TEXT UNIQUE,
  seerr_id INTEGER,             -- the Seerr user requests are made as
  jf_user TEXT,                 -- the Jellyfin user (name or id) they play as, set by an admin
  created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY,       -- sha256 of the cookie, so a leaked database isn't a login
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created REAL NOT NULL,
  seen REAL NOT NULL,
  agent TEXT
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS subs (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  channel_id TEXT NOT NULL,
  name TEXT, avatar TEXT, added REAL NOT NULL,
  PRIMARY KEY (user_id, channel_id)
);
CREATE TABLE IF NOT EXISTS history (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  video_id TEXT NOT NULL,
  item TEXT NOT NULL,           -- JSON card: title, channel, duration...
  position REAL NOT NULL DEFAULT 0,
  watched REAL NOT NULL,
  PRIMARY KEY (user_id, video_id)
);
CREATE TABLE IF NOT EXISTS later (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  video_id TEXT NOT NULL,
  item TEXT NOT NULL,
  added REAL NOT NULL,
  PRIMARY KEY (user_id, video_id)
);
CREATE TABLE IF NOT EXISTS prefs (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  key TEXT NOT NULL,
  value TEXT NOT NULL,          -- JSON
  PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS videos (
  id TEXT PRIMARY KEY,          -- YouTube id of a downloaded video
  item TEXT NOT NULL,
  path TEXT NOT NULL,           -- relative to VIDEOS
  size INTEGER, quality TEXT,
  user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  added REAL NOT NULL
);
-- the music folder's tag index (library.py); ids are hashes of the relative path, album and artist
CREATE TABLE IF NOT EXISTS tracks (
  id TEXT PRIMARY KEY,          -- tr-<hash of path>
  path TEXT NOT NULL UNIQUE,    -- relative to the music folder
  title TEXT NOT NULL, artist TEXT NOT NULL, album TEXT NOT NULL, album_artist TEXT NOT NULL,
  album_id TEXT NOT NULL, artist_id TEXT NOT NULL,   -- al-<hash>, ar-<hash>
  track INTEGER, disc INTEGER, year INTEGER, genre TEXT,
  secs REAL NOT NULL DEFAULT 0, bitrate INTEGER, size INTEGER NOT NULL, mtime REAL NOT NULL, suffix TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tracks_album ON tracks(album_id);
CREATE INDEX IF NOT EXISTS tracks_artist ON tracks(artist_id);
-- Subsonic API (subsonic.py): per-account stars, ratings and plays for tracks (tr-), albums (al-) and artists (ar-)
CREATE TABLE IF NOT EXISTS sub_meta (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  item TEXT NOT NULL,
  starred REAL, rating INTEGER, plays INTEGER NOT NULL DEFAULT 0, played REAL,
  PRIMARY KEY (user_id, item)
);
CREATE TABLE IF NOT EXISTS sub_playlists (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL, comment TEXT, public INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, changed REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sub_playlist_tracks (
  playlist_id INTEGER NOT NULL REFERENCES sub_playlists(id) ON DELETE CASCADE,
  pos INTEGER NOT NULL, track_id TEXT NOT NULL,
  PRIMARY KEY (playlist_id, pos)
);
CREATE TABLE IF NOT EXISTS sub_queue (
  user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  tracks TEXT NOT NULL, current TEXT, position INTEGER, changed REAL NOT NULL, changed_by TEXT
);
CREATE TABLE IF NOT EXISTS sub_bookmarks (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  track_id TEXT NOT NULL, position INTEGER NOT NULL, comment TEXT, created REAL NOT NULL, changed REAL NOT NULL,
  PRIMARY KEY (user_id, track_id)
);
-- passwords for music apps: the Subsonic token login (md5 of secret + salt) needs the secret itself, so these
-- are random and separate from the account password; the secret also works as an OpenSubsonic apiKey
CREATE TABLE IF NOT EXISTS app_passwords (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL, secret TEXT NOT NULL, key_hash TEXT NOT NULL UNIQUE, created REAL NOT NULL, used REAL
);
"""

COLUMNS = [
    ("videos", "keep", "INTEGER NOT NULL DEFAULT 0"),  # 1: never deleted by the retention rule
    ("users", "jf_user", "TEXT"),  # the Jellyfin user (name or id) they play as, set by an admin
]

_local = threading.local()
_schema_lock = threading.Lock()
_ready: set = set()


def db() -> sqlite3.Connection:
    """This thread's connection (sqlite connections can't be shared across threads)."""
    path = DATA / "yeerr.db"
    conn = getattr(_local, "db", None)
    if conn is None or getattr(_local, "path", None) != path:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        with _schema_lock:
            if path not in _ready:
                conn.executescript(SCHEMA)
                for table, column, decl in COLUMNS:  # columns added after a table first shipped
                    if column not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
                        try:
                            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
                        except sqlite3.OperationalError:  # another process added it first
                            pass
                _ready.add(path)
        _local.db, _local.path = conn, path
    return conn


def rows(sql, *args):
    return [dict(r) for r in db().execute(sql, args)]


def one(sql, *args):
    r = db().execute(sql, args).fetchone()
    return dict(r) if r else None


# --- settings: stored by an admin, with environment variables as defaults ----

ENV_DEFAULTS = {
    "seerr_url": "SEERR_URL",
    "seerr_key": "SEERR_API_KEY",
    "jellyfin_url": "JELLYFIN_URL",
    "jellyfin_key": "JELLYFIN_API_KEY",
    "jellyfin_user": "JELLYFIN_USER",
    "open_signup": "OPEN_SIGNUP",
    "video_keep_days": "VIDEO_KEEP_DAYS",
}


def setting(key: str, default: str = "") -> str:
    row = one("SELECT value FROM settings WHERE key = ?", key)
    if row and row["value"] is not None:
        return row["value"]
    return os.environ.get(ENV_DEFAULTS.get(key, ""), default) if key in ENV_DEFAULTS else default


def set_setting(key: str, value: str):
    db().execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, value))


# --- passwords and sessions ---------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def check_password(password: str, stored) -> bool:
    try:
        kind, salt, digest = (stored or "").split("$")
        mine = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32)
    except (ValueError, TypeError):
        return False
    return kind == "scrypt" and hmac.compare_digest(mine.hex(), digest)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_session(user_id: int, agent: str = "") -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    db().execute("INSERT INTO sessions(token, user_id, created, seen, agent) VALUES (?, ?, ?, ?, ?)",
                 (_digest(token), user_id, now, now, agent[:200]))
    return token


def session_user(token: str):
    if not token:
        return None
    row = one("""SELECT u.id, u.name, u.admin, u.jellyfin_id, u.seerr_id, u.pw IS NOT NULL AS local, s.seen
                 FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ?""", _digest(token))
    if not row:
        return None
    now = time.time()
    if now - row["seen"] > SESSION_DAYS * 86400:
        drop_session(token)
        return None
    if now - row["seen"] > 3600:  # sliding expiry, written at most hourly
        db().execute("UPDATE sessions SET seen = ? WHERE token = ?", (now, _digest(token)))
    row.pop("seen")
    row["admin"], row["local"] = bool(row["admin"]), bool(row["local"])
    return row


def drop_session(token: str):
    db().execute("DELETE FROM sessions WHERE token = ?", (_digest(token or ""),))


def set_cookie(response, request: Request, token: str):
    https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax", secure=https, path="/")


# --- request helpers ------------------------------------------------------------

def me(request: Request) -> dict:
    """The signed-in user (the middleware already refused anyone else)."""
    return request.state.user


def admin(request: Request) -> dict:
    user = request.state.user
    if not user["admin"]:
        raise HTTPException(403, "Only an admin can do that")
    return user


NAME = re.compile(r"^[\w .@-]{1,40}$")


def valid_name(name: str) -> str:
    name = (name or "").strip()
    if not NAME.match(name):
        raise HTTPException(422, "Names are 1 to 40 letters, numbers, spaces or . @ - _")
    return name


def valid_password(password: str) -> str:
    if len(password or "") < 8:
        raise HTTPException(422, "Passwords need at least 8 characters")
    return password


def dumps(x) -> str:
    return json.dumps(x, separators=(",", ":"))
