"""Private items: a download only its owner, and the people they share it with, can open.

Each person who turns this on gets an X25519 key pair. The private half is sealed twice, by their password and by a
recovery code shown once, and is only ever opened in server memory for a signed-in session (sign-in, or Unlock
after a restart). Each private file is encrypted with its own key, in 64 KiB AES-256-GCM chunks (the STREAM layout:
nonce = 7-byte file prefix + 4-byte counter + a last-chunk flag, the header as every chunk's associated data, so
chunks can't be reordered, dropped or cut off), and that key is sealed to each person it's shared with (ephemeral
X25519, HKDF-SHA256, AES-256-GCM). Without a key that's shared with you there's nothing to read, admins included.
Private files live in a folder Jellyfin and the music apps skip, and play only through Parlor."""
import hashlib
import json
import math
import os
import secrets
import struct
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

import auth
import core
from core import db, me, one, rows

# --- the file format ------------------------------------------------------------------------------

MAGIC, VERSION, CHUNK, TAG = b"PARLOR-PRIV-1\n", 1, 64 * 1024, 16
HEADER = len(MAGIC) + 5 + 7


class Broken(Exception):
    """The file was changed, cut short or reordered, or this isn't its key."""


def _nonce(prefix: bytes, i: int, last: bool) -> bytes:
    return prefix + i.to_bytes(4, "big") + (b"\x01" if last else b"\x00")


def encrypt(src, dst, key: bytes, prefix: Optional[bytes] = None):
    """Encrypt the open file src into the open file dst."""
    prefix = prefix or secrets.token_bytes(7)
    header = MAGIC + struct.pack(">BI", VERSION, CHUNK) + prefix
    dst.write(header)
    aead, i, cur = AESGCM(key), 0, src.read(CHUNK)
    while True:
        nxt = src.read(CHUNK)
        dst.write(aead.encrypt(_nonce(prefix, i, not nxt), cur, header))
        if not nxt:
            return
        cur, i = nxt, i + 1


def _header(f) -> bytes:
    f.seek(0)
    header = f.read(HEADER)
    if len(header) != HEADER or not header.startswith(MAGIC) or struct.unpack(">BI", header[len(MAGIC):len(MAGIC) + 5]) != (VERSION, CHUNK):
        raise Broken("not a private file")
    return header


def size_of(path: Path) -> int:
    body = path.stat().st_size - HEADER
    chunks = max(1, math.ceil(body / (CHUNK + TAG)))
    return body - chunks * TAG


def read_range(path: Path, key: bytes, start: int = 0, end: Optional[int] = None):
    """The plaintext bytes start..end (inclusive), decrypting only the chunks they're in."""
    with open(path, "rb") as f:
        header = _header(f)
        prefix, aead = header[-7:], AESGCM(key)
        body = path.stat().st_size - HEADER
        chunks = max(1, math.ceil(body / (CHUNK + TAG)))
        total = body - chunks * TAG
        end = total - 1 if end is None else min(end, total - 1)
        for i in range(start // CHUNK, end // CHUNK + 1 if total else 1):
            f.seek(HEADER + i * (CHUNK + TAG))
            try:
                plain = aead.decrypt(_nonce(prefix, i, i == chunks - 1), f.read(CHUNK + TAG), header)
            except Exception as exc:
                raise Broken("the file was changed, cut short or reordered, or this isn't its key") from exc
            lo, hi = max(start - i * CHUNK, 0), min(end - i * CHUNK + 1, len(plain))
            if lo < hi:
                yield plain[lo:hi]


# --- keys ---------------------------------------------------------------------------------------

SCRYPT = {"n": 2**15, "r": 8, "p": 1, "maxmem": 2**26, "dklen": 32}


def seal_to(pub: bytes, secret: bytes) -> bytes:
    """A sealed box: only the holder of pub's private half opens it."""
    eph = X25519PrivateKey.generate()
    eph_pub = eph.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    shared = eph.exchange(X25519PublicKey.from_public_bytes(pub))
    k = HKDF(hashes.SHA256(), 32, salt=eph_pub + pub, info=b"parlor-private-1").derive(shared)
    return eph_pub + AESGCM(k).encrypt(b"\x00" * 12, secret, None)  # the key is new for every box, so a fixed nonce is safe


def open_box(priv: X25519PrivateKey, box: bytes) -> bytes:
    eph_pub, pub = box[:32], _pub(priv)
    k = HKDF(hashes.SHA256(), 32, salt=eph_pub + pub, info=b"parlor-private-1").derive(priv.exchange(X25519PublicKey.from_public_bytes(eph_pub)))
    try:
        return AESGCM(k).decrypt(b"\x00" * 12, box[32:], None)
    except Exception as exc:
        raise Broken("not sealed to this key") from exc


def _pub(priv) -> bytes:
    return priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _wrap(secret: bytes, phrase: str) -> bytes:
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    return salt + nonce + AESGCM(hashlib.scrypt(phrase.encode(), salt=salt, **SCRYPT)).encrypt(nonce, secret, b"parlor person key")


def _unwrap(blob: bytes, phrase: str) -> bytes:
    kek = hashlib.scrypt(phrase.encode(), salt=blob[:16], **SCRYPT)
    try:
        return AESGCM(kek).decrypt(blob[16:28], blob[28:], b"parlor person key")
    except Exception as exc:
        raise Broken("wrong password or code") from exc


_held: dict = {}  # session digest -> (user id, X25519 private key): only in memory, only while signed in
_lock = threading.Lock()


def hold(user_id: int, password: str, token: str) -> bool:
    """At sign-in: open the person's private key with their password for this session. False when it doesn't open
    (an admin reset their password: the recovery code is needed)."""
    row = one("SELECT sealed_pw FROM person_keys WHERE user_id = ?", user_id)
    if not row or not token:
        return False
    try:
        priv = X25519PrivateKey.from_private_bytes(_unwrap(row["sealed_pw"], password))
    except Broken:
        return False
    with _lock:
        live = {r["token"] for r in rows("SELECT token FROM sessions")}
        for d in [d for d in _held if d not in live]:  # sessions that ended or expired
            _held.pop(d, None)
        _held[core._digest(token)] = (user_id, priv)
    return True


def drop(token: str):
    drop_digest(core._digest(token or ""))


def drop_digest(digest: str):
    """The same, for a session known by its stored digest (the devices list signs one out)."""
    _held.pop(digest, None)


def drop_user(user_id: int):
    """Every session of theirs ended (signed out everywhere, an admin's reset, a deleted account): their key goes too."""
    with _lock:
        for d in [d for d, (u, _) in _held.items() if u == user_id]:
            _held.pop(d, None)


def rewrap(user_id: int, old: str, new: str):
    """A password change: seal the private key by the new password (the old one opens it)."""
    row = one("SELECT sealed_pw FROM person_keys WHERE user_id = ?", user_id)
    if row:
        try:
            raw = _unwrap(row["sealed_pw"], old)
        except Broken:
            return  # already sealed by something else (an admin reset): the recovery code fixes that
        db().execute("UPDATE person_keys SET sealed_pw = ? WHERE user_id = ?", (_wrap(raw, new), user_id))


def _key(request: Request, user) -> X25519PrivateKey:
    held = _held.get(core._digest(core.session_cookie(request)))
    if held and held[0] == user["id"]:
        return held[1]
    if not one("SELECT 1 AS x FROM person_keys WHERE user_id = ?", user["id"]):
        raise HTTPException(409, "Turn on private items first (Settings → Private items)")
    raise HTTPException(403, "Enter your password to open your private items")  # not 423: the site reads that as the vault lock


# --- items ----------------------------------------------------------------------------------------

_changing = threading.Lock()  # ponytail: one lock for every change to a private file; per-item locks if many re-key at once


@contextmanager
def _tx():
    con = db()
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


def _write(dst: Path, parts, key: bytes):
    """Encrypt into dst by way of a .part file, on disk before it's renamed into place."""
    tmp = dst.with_suffix(".part")
    try:
        with open(tmp, "wb") as d:
            encrypt(_Reader(parts), d, key)
            d.flush()
            os.fsync(d.fileno())
        os.replace(tmp, dst)
    finally:
        tmp.unlink(missing_ok=True)


def sweep():
    """Files nothing points to: a copy a crash left half-made or half-swapped, or a deleted account's."""
    f = core.VIDEOS / ".parlor-private"
    if not f.is_dir():
        return
    with _changing:
        keep = {r["file"] for r in rows("SELECT file FROM private_items")} | {".ignore"}
        for p in f.iterdir():
            if p.is_file() and p.name not in keep:
                p.unlink(missing_ok=True)


def folder() -> Path:
    """Where private files live: inside the video folder but out of Jellyfin's and the music apps' reach."""
    f = core.VIDEOS / ".parlor-private"
    if not f.is_dir():
        f.mkdir(parents=True, exist_ok=True)
        (f / ".ignore").write_text("")  # Jellyfin skips a folder with this file
    return f


def _item(iid: str, request: Request, user) -> tuple:
    """(row, the item's file key) when it's shared with this person, else 404: others can't tell it exists."""
    row = one("SELECT i.*, s.wrapped FROM private_items i JOIN private_shares s ON s.item_id = i.id AND s.user_id = ? WHERE i.id = ?",
              user["id"], iid)
    if not row:
        raise HTTPException(404, "No such item")
    return row, open_box(_key(request, user), row["wrapped"])


def _meta(row, key) -> dict:
    nonce, blob = row["meta"][:12], row["meta"][12:]
    return json.loads(AESGCM(key).decrypt(nonce, blob, row["id"].encode()))


def _seal_meta(iid: str, key: bytes, meta: dict) -> bytes:
    nonce = secrets.token_bytes(12)
    return nonce + AESGCM(key).encrypt(nonce, json.dumps(meta).encode(), iid.encode())


def make_private(user, src: Path, meta: dict) -> str:
    """Encrypt src into the private folder for its owner, check it, then delete the plain file."""
    pub = one("SELECT pub FROM person_keys WHERE user_id = ?", user["id"])
    if not pub:
        raise HTTPException(409, "Turn on private items first (Settings → Private items)")
    with _changing:
        if not src.is_file():
            raise HTTPException(409, "It's private already, or gone")
        iid, key = secrets.token_hex(16), secrets.token_bytes(32)
        dst = folder() / iid
        _write(dst, _blocks(src), key)
        if _sha(read_range(dst, key)) != _sha(_blocks(src)):
            dst.unlink(missing_ok=True)
            raise HTTPException(500, "The encrypted copy didn't check out; the original is untouched")
        try:
            with _tx() as con:
                con.execute("INSERT INTO private_items(id, owner, file, size, meta, created) VALUES (?, ?, ?, ?, ?, ?)",
                            (iid, user["id"], dst.name, src.stat().st_size, _seal_meta(iid, key, meta), time.time()))
                con.execute("INSERT INTO private_shares(item_id, user_id, wrapped) VALUES (?, ?, ?)", (iid, user["id"], seal_to(pub["pub"], key)))
        except BaseException:
            dst.unlink(missing_ok=True)
            raise
        src.unlink()
    return iid


def _blocks(path: Path):
    with open(path, "rb") as f:
        yield from iter(lambda: f.read(1 << 20), b"")


def _sha(parts) -> bytes:
    h = hashlib.sha256()
    for part in parts:
        h.update(part)
    return h.digest()


router = APIRouter(prefix="/api/private")


class Password(BaseModel):
    password: str


class Recover(BaseModel):
    code: str
    password: str


class Share(BaseModel):
    name: str


@router.get("/state")
def state(request: Request, user=Depends(me)):
    on = one("SELECT 1 AS x FROM person_keys WHERE user_id = ?", user["id"]) is not None
    held = _held.get(core._digest(core.session_cookie(request)))
    return {"on": on, "open": bool(held and held[0] == user["id"])}


@router.post("/enable")
def enable(body: Password, request: Request, user=Depends(me)):
    """Make this person's key pair; the recovery code is shown this once."""
    if one("SELECT 1 AS x FROM person_keys WHERE user_id = ?", user["id"]):
        raise HTTPException(409, "Private items are on already")
    if not auth.confirm_password(user, body.password):
        raise HTTPException(403, "That isn't your password")
    priv = X25519PrivateKey.generate()
    raw = priv.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    code = "-".join(secrets.token_hex(16)[i:i + 4] for i in range(0, 32, 4))
    db().execute("INSERT INTO person_keys(user_id, pub, sealed_pw, sealed_code, created) VALUES (?, ?, ?, ?, ?)",
                 (user["id"], _pub(priv), _wrap(raw, body.password), _wrap(raw, code), time.time()))
    hold(user["id"], body.password, core.session_cookie(request))
    return {"recovery_code": code}


@router.post("/unlock")
def unlock(body: Password, request: Request, user=Depends(me)):
    """After a restart, or with a new password an admin set: open the key for this session."""
    if not auth.confirm_password(user, body.password):
        raise HTTPException(403, "That isn't your password")
    if not hold(user["id"], body.password, core.session_cookie(request)):
        raise HTTPException(409, "Your private items are sealed by an older password: enter your recovery code")
    return state(request, user)


@router.post("/recover")
def recover(body: Recover, request: Request, user=Depends(me)):
    """The recovery code opens the key, which is then sealed by the password you sign in with now."""
    if not auth.confirm_password(user, body.password):
        raise HTTPException(403, "That isn't your password")
    row = one("SELECT sealed_code FROM person_keys WHERE user_id = ?", user["id"])
    if not row:
        raise HTTPException(409, "Private items aren't on")
    try:
        raw = _unwrap(row["sealed_code"], body.code.strip().lower())
    except Broken:
        raise HTTPException(403, "That isn't your recovery code") from None
    db().execute("UPDATE person_keys SET sealed_pw = ? WHERE user_id = ?", (_wrap(raw, body.password), user["id"]))
    hold(user["id"], body.password, core.session_cookie(request))
    return state(request, user)


@router.get("")
def listing(request: Request, user=Depends(me)):
    """What's shared with you (yours included). Others never see these titles."""
    if not one("SELECT 1 AS x FROM person_keys WHERE user_id = ?", user["id"]):
        return {"items": []}  # nothing can be shared with someone who has no key
    _key(request, user)  # open, or say how to open it
    items = []
    for row in rows("SELECT i.*, s.wrapped FROM private_items i JOIN private_shares s ON s.item_id = i.id AND s.user_id = ? "
                    "ORDER BY i.created DESC", user["id"]):
        key = open_box(_key(request, user), row["wrapped"])
        mine = row["owner"] == user["id"]
        shared = [r["name"] for r in rows("SELECT u.name FROM private_shares s JOIN users u ON u.id = s.user_id "
                                          "WHERE s.item_id = ? AND s.user_id != ?", row["id"], row["owner"])] if mine else []
        items.append({"id": row["id"], "mine": mine, "size": row["size"], "added": row["created"],
                      "shared_with": shared, **_meta(row, key)})  # only the owner sees who else has it
    return {"items": items}


@router.get("/{iid}/file")
def file(iid: str, request: Request, user=Depends(me)):
    from yt import parse_range  # late: yt imports this module
    row, key = _item(iid, request, user)
    path = folder() / row["file"]
    size = row["size"]
    kind = _meta(row, key).get("type") or "application/octet-stream"
    if not size:
        return Response(b"", media_type=kind, headers={"Cache-Control": "no-store"})
    start, end = parse_range(request.headers.get("range"), size) or (0, size - 1)
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "no-store", "Content-Length": str(max(0, end - start + 1))}
    status = 200
    if request.headers.get("range"):
        status, headers["Content-Range"] = 206, f"bytes {start}-{end}/{size}"
    return StreamingResponse(read_range(path, key, start, end), status_code=status, media_type=kind, headers=headers)


@router.post("/{iid}/share")
def share(iid: str, body: Share, request: Request, user=Depends(me)):
    with _changing:  # not while it's being re-keyed
        row, key = _item(iid, request, user)
        if row["owner"] != user["id"]:
            raise HTTPException(403, "Only its owner shares it")
        them = one("SELECT u.id, k.pub FROM users u JOIN person_keys k ON k.user_id = u.id WHERE u.name = ?", body.name.strip())
        if not them:  # one answer for both, so it doesn't tell which names exist
            raise HTTPException(404, f"No one called {body.name.strip()} has private items turned on (Settings)")
        db().execute("INSERT OR REPLACE INTO private_shares(item_id, user_id, wrapped) VALUES (?, ?, ?)", (iid, them["id"], seal_to(them["pub"], key)))
    return {"ok": True}


@router.delete("/{iid}/share/{name}")
def unshare(iid: str, name: str, request: Request, user=Depends(me)):
    """Take someone off it. The file gets a new key, so a key they kept from before opens nothing. The new copy is
    a new file and the database moves to it in one transaction, so a crash at any point leaves one whole copy."""
    with _changing:
        row, key = _item(iid, request, user)
        if row["owner"] != user["id"]:
            raise HTTPException(403, "Only its owner changes who has it")
        them = one("SELECT u.id FROM users u JOIN private_shares s ON s.user_id = u.id AND s.item_id = ? WHERE u.name = ?", iid, name)
        if not them or them["id"] == user["id"]:
            raise HTTPException(404, "Not shared with them")
        new, old = secrets.token_bytes(32), folder() / row["file"]
        dst = folder() / secrets.token_hex(16)
        _write(dst, read_range(old, key), new)
        try:
            with _tx() as con:
                con.execute("DELETE FROM private_shares WHERE item_id = ? AND user_id = ?", (iid, them["id"]))
                for s in rows("SELECT s.user_id, k.pub FROM private_shares s JOIN person_keys k ON k.user_id = s.user_id WHERE s.item_id = ?", iid):
                    con.execute("UPDATE private_shares SET wrapped = ? WHERE item_id = ? AND user_id = ?", (seal_to(s["pub"], new), iid, s["user_id"]))
                con.execute("UPDATE private_items SET file = ?, meta = ? WHERE id = ?", (dst.name, _seal_meta(iid, new, _meta(row, key)), iid))
        except BaseException:
            dst.unlink(missing_ok=True)
            raise
        old.unlink(missing_ok=True)
    return {"ok": True}


@router.post("/{iid}/unprivate")
def unprivate(iid: str, request: Request, user=Depends(me)):
    """Back into the shared library, decrypted, where it was."""
    with _changing:
        row, key = _item(iid, request, user)
        if row["owner"] != user["id"]:
            raise HTTPException(403, "Only its owner can do that")
        meta = _meta(row, key)
        dst = (core.VIDEOS / meta["path"]).resolve()
        if not dst.is_relative_to(core.VIDEOS.resolve()) or dst.exists():
            raise HTTPException(409, "Something else is in its place in the library")
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".part")
        try:
            with open(tmp, "wb") as d:
                for part in read_range(folder() / row["file"], key):
                    d.write(part)
            os.replace(tmp, dst)
        finally:
            tmp.unlink(missing_ok=True)  # no plain half-copy left in the shared folder
        with _tx() as con:
            con.execute("INSERT OR REPLACE INTO videos(id, item, path, size, quality, user_id, added) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (meta["vid"], meta["item"], meta["path"], row["size"], meta.get("quality"), user["id"], time.time()))
            con.execute("DELETE FROM private_items WHERE id = ?", (iid,))
        (folder() / row["file"]).unlink(missing_ok=True)
    return {"ok": True, "id": meta["vid"]}


class _Reader:
    """A file-like read() over a generator of byte strings."""

    def __init__(self, parts):
        self.parts, self.buf = iter(parts), b""

    def read(self, n):
        while len(self.buf) < n:
            nxt = next(self.parts, None)
            if nxt is None:
                break
            self.buf += nxt
        out, self.buf = self.buf[:n], self.buf[n:]
        return out
