"""Settings → Security: encryption at rest, the key's backup, and lock at boot (Parlor starts locked
until the owner's passphrase opens the key)."""
import collections
import threading
import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

import auth
import core
import vault

router = APIRouter(prefix="/api/vault")
PUBLIC = {"/api/vault/state", "/api/vault/unlock"}  # what answers while Parlor is locked (besides /api/health)
TRIES, TRY_WINDOW = 5, 60  # wrong passphrases a minute from one address, then it waits
_tries: dict = collections.defaultdict(collections.deque)
_trying = threading.Lock()  # one passphrase at a time: each costs ~0.5 s and 128 MB to try
on_unlock = lambda: None  # noqa: E731  app.py: the start that waited for the key


class Unlock(BaseModel):
    passphrase: str


class Confirm(BaseModel):
    password: str


class Passphrase(BaseModel):
    password: str
    passphrase: str = ""


@router.get("/state")
def state():
    return {"locked": vault.locked(), "encrypted": vault.on(), "lock_at_boot": vault.on() and vault.lock_at_boot(),
            "key_apart": vault.key_apart(core.DATA)}


@router.post("/unlock")
def unlock(body: Unlock, request: Request):
    if not _trying.acquire(blocking=False):  # another attempt is being tried: no queue of them, no memory pile-up
        raise HTTPException(429, "Another unlock is being tried; try again in a moment")
    try:
        if not vault.locked():
            return {"ok": True}
        now, mine = time.time(), _tries[auth._client(request)]  # per address, so someone guessing can't lock the owner out
        while mine and now - mine[0] > TRY_WINDOW:
            mine.popleft()
        if len(mine) >= TRIES:
            raise HTTPException(429, "Too many wrong passphrases. Wait a minute and try again.")
        try:
            vault.unlock(body.passphrase)
        except vault.WrongKey:
            mine.append(now)
            raise HTTPException(403, "That isn't the passphrase") from None
        _tries.clear()
        on_unlock()  # once: a second unlock finds it open
        return {"ok": True}
    finally:
        _trying.release()


def _confirmed(request: Request, password: str) -> dict:
    user = core.admin(request)
    if not auth.confirm_password(user, password):
        raise HTTPException(403, "That isn't your password")
    return user


@router.post("/key")
def backup(body: Confirm, request: Request, response: Response):
    """The key, for a backup somewhere safe: with it, a copy of Parlor's data can be read."""
    _confirmed(request, body.password)
    if not vault.on():
        raise HTTPException(409, "Encryption is off (ENCRYPT=0): there's no key")
    response.headers["Cache-Control"] = "no-store"
    return {"key": vault.key().hex(), "file": "db.key"}


@router.put("/passphrase")
def passphrase(body: Passphrase, request: Request):
    """Lock at boot with this passphrase, or "" to start unlocked again."""
    _confirmed(request, body.password)
    if not vault.on():
        raise HTTPException(409, "Encryption is off (ENCRYPT=0)")
    if body.passphrase and len(body.passphrase) < 12:
        raise HTTPException(422, "Use at least 12 characters: it's the only thing between a copy of the data and its contents")
    vault.set_passphrase(body.passphrase)
    return state()


def is_open(path: str) -> bool:
    """While locked, only health, the lock's own state and unlocking answer."""
    return not vault.locked() or path in PUBLIC or path == "/api/health"
