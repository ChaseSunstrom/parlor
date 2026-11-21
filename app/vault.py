"""Encryption at rest for what Parlor writes: its database (SQLCipher) and the few files it keeps
beside it (YouTube cookies, the download list), all with one random 256-bit key.

The key lives in KEY_DIR/db.key, made on the first start. The compose mounts ./parlor/key there,
apart from ./parlor/config, so a copy of the config folder alone is unreadable. Lose the key and
the data is gone: Settings offers it for backup. ENCRYPT=0 keeps everything plain."""
import base64
import hashlib
import hmac
import logging
import os
import secrets
import sqlite3
import tempfile
import threading
from pathlib import Path

try:
    import sqlcipher3.dbapi2 as sqlcipher
except ImportError:  # a dev setup without it: plain sqlite, said once in the log
    sqlcipher = None
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    AESGCM = None

log = logging.getLogger("parlor.vault")
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
KEY_DIR = Path(os.environ.get("KEY_DIR") or DATA_DIR / "key")
PLAIN = b"SQLite format 3\x00"
SEALED = b"PARLOR-SEALED-1\n"  # the start of every file this module writes
_key: bytes = b""
_converted = False  # this start converted the database: keep its plain copy until the next one
_lock = threading.RLock()  # the key, its file, and changing them
_db: Path | None = None  # the database connect() opened: its key is never replaced by a new one
OperationalError = (sqlite3.OperationalError,) + ((sqlcipher.OperationalError,) if sqlcipher else ())


class WrongKey(Exception):
    """The key doesn't open this database or file (a different key, or the file was changed)."""


class Locked(Exception):
    """The key is sealed by the owner's passphrase and nobody has entered it since the start."""


def on() -> bool:
    if os.environ.get("ENCRYPT", "1") == "0":
        return False
    if sqlcipher is None or AESGCM is None:
        if not getattr(on, "warned", False):
            log.warning("encryption is on, but sqlcipher3 or cryptography isn't installed: keeping data plain")
            on.warned = True
        return False
    return True


def forget():
    """Drop the key from memory, as a new start would (tests, and lock at boot)."""
    global _key, _converted, _db
    _key, _converted, _db = b"", False, None


def row_type(conn):
    """The Row class that goes with this connection's module."""
    return sqlite3.Row if isinstance(conn, sqlite3.Connection) else sqlcipher.Row


def key_file() -> Path:
    return KEY_DIR / "db.key"


def _save_key_file(text: str):
    """Replace db.key atomically: a crash or a second writer never leaves half a key."""
    with _lock:
        KEY_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(KEY_DIR, 0o700)
        fd, tmp = tempfile.mkstemp(dir=KEY_DIR, prefix=".db.key.")  # 0600
        try:
            with os.fdopen(fd, "w") as f:
                f.write(text + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, key_file())
        finally:
            Path(tmp).unlink(missing_ok=True)


def _head(path: Path) -> bytes:
    with open(path, "rb") as f:
        return f.read(16)


def _encrypted(path) -> bool:
    return bool(path) and path.exists() and path.stat().st_size > 0 and _head(path) != PLAIN


def key() -> bytes:
    """The 32-byte key, made on the first start."""
    global _key
    with _lock:
        if not _key:
            path = key_file()
            if not path.exists():
                if _encrypted(_db):  # its key went missing (a moved or unmounted KEY_DIR): never make another
                    raise WrongKey(f"Parlor's database is encrypted, but its key isn't in {KEY_DIR}: put the backed-up db.key there")
                _save_key_file(secrets.token_hex(32))
                log.info("made the encryption key in %s: back it up, the data can't be read without it", path)
            text = path.read_text().strip()
            if text.startswith("scrypt$"):
                raise Locked("Parlor is locked: enter the passphrase")
            try:
                found = bytes.fromhex(text)
            except ValueError as exc:
                raise WrongKey(f"{path} isn't a key Parlor made") from exc
            if len(found) != 32:
                raise WrongKey(f"{path} isn't a key Parlor made")
            _key = found
        return _key


# --- lock at boot: the key on disk sealed by the owner's passphrase ------------------------------

SCRYPT = {"n": 2**17, "r": 8, "p": 1, "maxmem": 2**28, "dklen": 32}  # about half a second and 128 MB, once per start


def lock_at_boot() -> bool:
    return key_file().exists() and key_file().read_text().startswith("scrypt$")


def locked() -> bool:
    return on() and not _key and lock_at_boot()


def unlock(passphrase: str):
    """Open the sealed key with the passphrase (WrongKey if it isn't the one)."""
    global _key
    _, salt, nonce, sealed = key_file().read_text().strip().split("$")
    kek = hashlib.scrypt(passphrase.encode(), salt=bytes.fromhex(salt), **SCRYPT)
    try:
        found = AESGCM(kek).decrypt(bytes.fromhex(nonce), bytes.fromhex(sealed), b"parlor key")
    except Exception as exc:
        raise WrongKey("that isn't the passphrase") from exc
    with _lock:
        _key = found


def set_passphrase(passphrase: str):
    """Seal the key on disk with this passphrase, so Parlor starts locked; "" puts the plain key back."""
    with _lock:
        raw = key()
        if not passphrase:
            _save_key_file(raw.hex())
            return
        salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
        kek = hashlib.scrypt(passphrase.encode(), salt=salt, **SCRYPT)
        sealed = AESGCM(kek).encrypt(nonce, raw, b"parlor key")
        _save_key_file(f"scrypt${salt.hex()}${nonce.hex()}${sealed.hex()}")


def key_apart(data: Path) -> bool:
    """The key isn't inside the data folder, so a copy of that folder alone can't be read."""
    return not KEY_DIR.resolve().is_relative_to(data.resolve())


# --- the database ------------------------------------------------------------------------------

def _keyed(conn):
    conn.execute(f"PRAGMA key = \"x'{key().hex()}'\"")  # a raw key: no slow passphrase stretching
    try:
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlcipher.DatabaseError as exc:
        raise WrongKey("the encryption key doesn't open Parlor's database") from exc
    return conn


def connect(path: Path, **kw):
    """A connection to Parlor's database: encrypted when on() (an old plain one is converted first)."""
    if not on():
        if sqlcipher and _encrypted(path):
            log.warning("ENCRYPT=0, but the database is already encrypted: it stays encrypted")
            return _keyed(sqlcipher.connect(str(path), **kw))
        return sqlite3.connect(path, **kw)
    global _db
    _db = path
    if path.exists() and _head(path) == PLAIN:
        convert(path)
    conn = _keyed(sqlcipher.connect(str(path), **kw))
    backup = path.with_name(path.name + ".plain-backup")
    if backup.exists() and not _converted:  # a good start after the one that converted it
        backup.unlink()
        log.info("removed the plain copy kept from the conversion")
    return conn


def _tables(conn):
    names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    return {n: conn.execute(f'SELECT count(*) FROM "{n}"').fetchone()[0] for n in names}


def _check(path: Path, want: dict):
    """The encrypted copy opens with the key, is whole, and has every row."""
    conn = _keyed(sqlcipher.connect(str(path)))
    try:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("the encrypted copy failed its integrity check")
        if _tables(conn) != want:
            raise RuntimeError("the encrypted copy is missing rows")
    finally:
        conn.close()


def convert(path: Path):
    """Encrypt a plain database in place: write an encrypted copy, check it, keep the plain one as
    .plain-backup until the next good start, then swap atomically. Until the swap the original is
    untouched, so a crash or a failed check changes nothing."""
    tmp, backup = path.with_name(path.name + ".encrypting"), path.with_name(path.name + ".plain-backup")
    tmp.unlink(missing_ok=True)
    src = sqlcipher.connect(str(path))  # no key: SQLCipher reads a plain database as it is
    try:
        src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        want = _tables(src)
        src.execute(f"ATTACH DATABASE ? AS sealed KEY \"x'{key().hex()}'\"", (str(tmp),))
        src.execute("SELECT sqlcipher_export('sealed')")
        src.execute("DETACH DATABASE sealed")
    finally:
        src.close()
    try:
        _check(tmp, want)
        with open(tmp, "rb") as f:
            os.fsync(f.fileno())
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(path.read_bytes())
            os.fsync(f.fileno())
        for side in ("-wal", "-shm"):
            path.with_name(path.name + side).unlink(missing_ok=True)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    global _converted
    _converted = True
    log.info("encrypted Parlor's database (the plain copy is kept as %s until the next start)", backup.name)


# --- files beside it ---------------------------------------------------------------------------

def _file_key() -> bytes:
    return hmac.new(key(), b"parlor files", hashlib.sha256).digest()  # its own key, derived from the one


def write(path: Path, data: bytes):
    """Write a file only Parlor can read: sealed with AES-GCM when on(), mode 0600 either way, atomically."""
    if on():
        nonce = secrets.token_bytes(12)
        data = SEALED + base64.b64encode(nonce + AESGCM(_file_key()).encrypt(nonce, data, path.name.encode()))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def read(path: Path) -> bytes:
    """A file write() made (or a plain one from before encryption, as it is)."""
    raw = path.read_bytes()
    if not raw.startswith(SEALED):
        return raw
    try:
        blob = base64.b64decode(raw[len(SEALED):])
        return AESGCM(_file_key()).decrypt(blob[:12], blob[12:], path.name.encode())
    except Exception as exc:  # cryptography's InvalidTag: another key, or the file was changed
        raise WrongKey(f"{path.name} can't be opened with this key") from exc


OWN = ("yeerr.db", "yeerr.db-wal", "yeerr.db-shm", "yeerr.db.plain-backup", "yt-downloads.json", "cookies", "cache", "covers", "backups", "key")


def lock_down(folder: Path):
    """Parlor's own files in its data folder for Parlor alone (0700 folders, 0600 files), and the folder
    itself when it holds nothing else (a DATA_DIR shared with other things keeps its own mode). Only
    what Parlor makes is touched; one that won't change (a read-only or network folder) is left as it is."""
    try:
        for name in OWN:
            p = folder / name
            if p.is_symlink() or not p.exists():
                continue
            os.chmod(p, 0o700 if p.is_dir() else 0o600)
            if name == "cookies":
                for c in p.iterdir():
                    if c.is_file() and not c.is_symlink():
                        os.chmod(c, 0o600)
        if all(p.name in OWN or p.name.startswith("yeerr.db") for p in folder.iterdir()):
            os.chmod(folder, 0o700)
    except OSError as exc:
        log.warning("couldn't make %s private: %s", folder, exc)
