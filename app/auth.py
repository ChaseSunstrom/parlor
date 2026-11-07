"""Accounts: first-run admin setup, sign in (local or Jellyfin), users and settings."""

import re
import time
import uuid
from collections import defaultdict
from typing import Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from core import (COOKIE, _digest, admin, check_password, db, drop_session, hash_password, me, new_session, one, rows,
                  set_cookie, set_setting, setting, valid_name, valid_password)
import stack

router = APIRouter(prefix="/api")

# a few wrong passwords per name or address, then a pause: slows down guessing
_failures: dict = defaultdict(list)
FAIL_LIMIT, FAIL_WINDOW = 8, 15 * 60


def _throttled(*keys) -> bool:
    now = time.time()
    if len(_failures) > 10000:  # bounded: forget everything stale
        for key in [k for k, ts in _failures.items() if not ts or now - ts[-1] >= FAIL_WINDOW]:
            del _failures[key]
    for key in keys:
        _failures[key] = [t for t in _failures.get(key, []) if now - t < FAIL_WINDOW]
        if not _failures[key]:
            del _failures[key]
    return any(len(_failures.get(key, [])) >= FAIL_LIMIT for key in keys)


def _failed(*keys):
    for key in keys:
        _failures[key].append(time.time())


class Login(BaseModel):
    name: str
    password: str


def _client(request: Request) -> str:
    return request.client.host if request.client else "?"


def _signed_in(request: Request, response: Response, user_id: int):
    token = new_session(user_id, request.headers.get("user-agent", ""))
    set_cookie(response, request, token)
    return {"ok": True}


def users_exist() -> bool:
    return one("SELECT 1 AS x FROM users LIMIT 1") is not None


@router.get("/auth/state")
def auth_state():
    """What the sign-in screen should offer (anyone may ask)."""
    return {
        "setup": not users_exist(),
        "signup": setting("open_signup") in ("1", "true", "yes"),
        "jellyfin": bool(setting("jellyfin_url")),
    }


@router.post("/auth/setup")
def setup(body: Login, request: Request, response: Response):
    """The very first account, which is the admin. Only works while there are no accounts."""
    name, password = valid_name(body.name), valid_password(body.password)
    con = db()
    con.execute("BEGIN IMMEDIATE")  # two first-run tabs can't both become admin
    try:
        if one("SELECT 1 AS x FROM users LIMIT 1"):
            raise HTTPException(409, "Setup is already done; sign in instead")
        cur = con.execute("INSERT INTO users(name, pw, admin, created) VALUES (?, ?, 1, ?)",
                          (name, hash_password(password), time.time()))
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    if stack.on():  # yeerr's compose: set every app up with this name and password (kept in memory only)
        stack.start(name, password, retries=10)
    return _signed_in(request, response, cur.lastrowid)


@router.post("/auth/signup")
def signup(body: Login, request: Request, response: Response):
    if setting("open_signup") not in ("1", "true", "yes"):
        raise HTTPException(403, "Sign-ups are closed; ask the admin for an account")
    if not users_exist():
        raise HTTPException(409, "Create the admin account first")
    user_id = create_user(body.name, body.password, False)
    return _signed_in(request, response, user_id)


@router.post("/auth/login")
def login(body: Login, request: Request, response: Response):
    name = (body.name or "").strip()
    if not users_exist():
        raise HTTPException(409, "Create the admin account first")  # before setup nobody, not even Jellyfin, may sign in
    keys = (f"name:{name.casefold()}", f"ip:{_client(request)}")
    if _throttled(*keys):
        raise HTTPException(429, "Too many wrong passwords. Wait a few minutes and try again.")
    user = one("SELECT id, pw, jellyfin_id FROM users WHERE name = ?", name)
    if user and user["pw"]:
        if check_password(body.password, user["pw"]):
            return _signed_in(request, response, user["id"])
    elif setting("jellyfin_url") and (user is None or user["jellyfin_id"]):
        user_id = jellyfin_login(name, body.password)
        if user_id:
            return _signed_in(request, response, user_id)
    else:
        check_password(body.password, "scrypt$00$00")  # same work either way, so timing doesn't reveal names
    _failed(*keys)
    raise HTTPException(401, "Wrong name or password")


def jellyfin_login(name: str, password: str) -> Optional[int]:
    """Sign in with a Jellyfin account; the first time, it gets an account here.
    Jellyfin admins are admins here too."""
    base = setting("jellyfin_url").rstrip("/")
    device = uuid.uuid5(uuid.NAMESPACE_URL, f"yeerr:{name}").hex
    auth = f'MediaBrowser Client="yeerr", Device="yeerr", DeviceId="{device}", Version="1.0"'
    try:
        r = requests.post(f"{base}/Users/AuthenticateByName", json={"Username": name, "Pw": password},
                          headers={"Authorization": auth}, timeout=15)
    except requests.RequestException as exc:
        raise HTTPException(502, f"Couldn't reach Jellyfin: {exc}") from exc
    if r.status_code in (400, 401, 403):
        return None
    if not r.ok:
        raise HTTPException(502, f"Jellyfin answered {r.status_code}")
    data = r.json()
    try:  # we only needed to know the password is right; don't leave a session behind in Jellyfin
        requests.post(f"{base}/Sessions/Logout", headers={"Authorization": f'{auth}, Token="{data["AccessToken"]}"'}, timeout=5)
    except requests.RequestException:
        pass
    jf = data["User"]
    linked = one("SELECT id FROM users WHERE jellyfin_id = ?", jf["Id"])
    if linked:
        return linked["id"]
    if one("SELECT 1 AS x FROM users WHERE name = ?", jf["Name"]):
        # never hand an existing local account to someone just because the names match
        raise HTTPException(409, f"An account called {jf['Name']} already exists here; sign in with its password")
    is_admin = bool(jf.get("Policy", {}).get("IsAdministrator"))
    cur = db().execute("INSERT INTO users(name, admin, jellyfin_id, created) VALUES (?, ?, ?, ?)",
                       (valid_name(jf["Name"]), int(is_admin), jf["Id"], time.time()))
    return cur.lastrowid


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    drop_session(request.cookies.get(COOKIE, ""))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.post("/auth/logout-everywhere")
def logout_everywhere(response: Response, user=Depends(me)):
    db().execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def whoami(user=Depends(me)):
    from yt import has_cookies  # late: yt imports this module's neighbours

    return {**user, "jellyfin": bool(user["jellyfin_id"]), "youtube_cookies": has_cookies(user["id"])}


class PasswordChange(BaseModel):
    old: str
    new: str


@router.post("/me/password")
def change_password(body: PasswordChange, request: Request, user=Depends(me)):
    row = one("SELECT pw FROM users WHERE id = ?", user["id"])
    if not row["pw"]:
        raise HTTPException(409, "This account signs in with Jellyfin; change the password there")
    if not check_password(body.old, row["pw"]):
        raise HTTPException(403, "Your current password isn't right")
    db().execute("UPDATE users SET pw = ? WHERE id = ?", (hash_password(valid_password(body.new)), user["id"]))
    # other devices have to sign in again with the new password
    keep = request.cookies.get(COOKIE, "")
    db().execute("DELETE FROM sessions WHERE user_id = ? AND token != ?", (user["id"], _digest(keep)))
    return {"ok": True}


# --- admin: users -----------------------------------------------------------------

def create_user(name, password, is_admin) -> int:
    name, password = valid_name(name), valid_password(password)
    if one("SELECT 1 AS x FROM users WHERE name = ?", name):
        raise HTTPException(409, f"There's already an account called {name}")
    cur = db().execute("INSERT INTO users(name, pw, admin, created) VALUES (?, ?, ?, ?)",
                       (name, hash_password(password), int(bool(is_admin)), time.time()))
    return cur.lastrowid


@router.get("/users")
def list_users(_=Depends(admin)):
    return rows("""SELECT u.id, u.name, u.admin, u.jellyfin_id IS NOT NULL AS jellyfin, u.seerr_id, u.jf_user, u.created,
                          (SELECT MAX(seen) FROM sessions s WHERE s.user_id = u.id) AS seen
                   FROM users u ORDER BY u.name""")


class NewUser(BaseModel):
    name: str
    password: str
    admin: bool = False


@router.post("/users")
def add_user(body: NewUser, _=Depends(admin)):
    return {"id": create_user(body.name, body.password, body.admin)}


class UserChange(BaseModel):
    admin: Optional[bool] = None
    password: Optional[str] = None
    seerr_id: Optional[int] = None
    unlink_seerr: bool = False
    jf_user: Optional[str] = None  # the Jellyfin user they play as; "" clears it


def _admins_left_without(user_id: int) -> int:
    return one("SELECT COUNT(*) AS n FROM users WHERE admin = 1 AND id != ?", user_id)["n"]


@router.patch("/users/{user_id}")
def change_user(user_id: int, body: UserChange, _=Depends(admin)):
    target = one("SELECT id, pw FROM users WHERE id = ?", user_id)
    if not target:
        raise HTTPException(404, "No such account")
    if body.admin is False and not _admins_left_without(user_id):
        raise HTTPException(409, "There has to be at least one admin")
    if body.admin is not None:
        db().execute("UPDATE users SET admin = ? WHERE id = ?", (int(body.admin), user_id))
    if body.password:
        if not target["pw"]:
            raise HTTPException(409, "This account signs in with Jellyfin")
        db().execute("UPDATE users SET pw = ? WHERE id = ?", (hash_password(valid_password(body.password)), user_id))
        db().execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    if body.seerr_id is not None or body.unlink_seerr:
        db().execute("UPDATE users SET seerr_id = ? WHERE id = ?", (None if body.unlink_seerr else body.seerr_id, user_id))
    if body.jf_user is not None:
        db().execute("UPDATE users SET jf_user = ? WHERE id = ?", (_jf_user(body.jf_user) or None, user_id))
    return {"ok": True}


@router.delete("/users/{user_id}")
def delete_user(user_id: int, user=Depends(admin)):
    if user_id == user["id"]:
        raise HTTPException(409, "You can't delete your own account")
    if not _admins_left_without(user_id):
        raise HTTPException(409, "There has to be at least one admin")
    db().execute("DELETE FROM users WHERE id = ?", (user_id,))
    from yt import cookie_path  # late: yt imports core, which this module also uses

    cookie_path(user_id).unlink(missing_ok=True)
    return {"ok": True}


# --- admin: settings ----------------------------------------------------------------

class Settings(BaseModel):
    seerr_url: Optional[str] = None
    seerr_key: Optional[str] = None
    jellyfin_url: Optional[str] = None
    jellyfin_key: Optional[str] = None
    jellyfin_user: Optional[str] = None  # who unlinked accounts play as
    open_signup: Optional[bool] = None


def _jf_user(value: str) -> str:
    value = (value or "").strip()
    if len(value) > 64:
        raise HTTPException(422, "Jellyfin user names are at most 64 characters")
    return value


def _url(value: str) -> str:
    value = (value or "").strip().rstrip("/")
    if value and not value.startswith(("http://", "https://")):
        raise HTTPException(422, "Addresses start with http:// or https://")
    return value


@router.get("/settings")
def get_settings(_=Depends(admin)):
    key = setting("seerr_key")
    return {
        "seerr_url": setting("seerr_url"),
        "seerr_key_set": bool(key),
        "seerr_key_hint": f"…{key[-4:]}" if len(key) > 8 else "",
        "jellyfin_url": setting("jellyfin_url"),
        "jellyfin_key_set": bool(setting("jellyfin_key")),  # the key itself is never sent back
        "jellyfin_user": setting("jellyfin_user"),
        "open_signup": setting("open_signup") in ("1", "true", "yes"),
    }


@router.put("/settings")
def put_settings(body: Settings, _=Depends(admin)):
    if body.seerr_url is not None:
        set_setting("seerr_url", _url(body.seerr_url))
    if body.seerr_key is not None:
        set_setting("seerr_key", body.seerr_key.strip())
    if body.jellyfin_url is not None:
        set_setting("jellyfin_url", _url(body.jellyfin_url))
    if body.jellyfin_key is not None:
        key = body.jellyfin_key.strip()
        if not re.fullmatch(r"[\w.=+/-]{0,128}", key, re.ASCII):
            raise HTTPException(422, "That doesn't look like a Jellyfin API key")
        set_setting("jellyfin_key", key)
    if body.jellyfin_user is not None:
        set_setting("jellyfin_user", _jf_user(body.jellyfin_user))
    if body.open_signup is not None:
        set_setting("open_signup", "1" if body.open_signup else "0")
    return get_settings(_)
