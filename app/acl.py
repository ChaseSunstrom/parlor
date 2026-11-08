"""Who may use what: an admin picks, per person, the sections of Parlor they can use and the Jellyfin libraries
they see. Sections are enforced here, in one place every request goes through (app.py's guard, and Subsonic's
own login); libraries are enforced by Jellyfin itself, through the person's own Jellyfin user, which Parlor makes
and keeps in sync. Admins always have everything; people from before this have everything until it's changed."""
import json
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import core
from core import admin, db, one

SECTIONS = {"watch": "Watch", "media": "Movies & TV", "music": "Music", "library": "Library", "downloads": "Downloads"}
EVERYONE, ADMIN = ("everyone",), ("admin",)  # no section: any signed-in person; or admins only (core.admin decides)
# path prefix -> sections it needs (all of them), first match wins: the specific ones first
RULES = [
    ("/api/yt/downloads", ("watch", "downloads")), ("/api/yt/library", ("watch", "downloads")), ("/api/yt/file/", ("watch", "downloads")),
    ("/api/yt/", ("watch",)),
    ("/api/me/history", ("watch",)), ("/api/me/later", ("watch",)), ("/api/me/subs", ("watch",)), ("/api/me/topics", ("watch",)),
    ("/api/me/youtube-cookies", ("watch",)),
    ("/api/private", ("watch", "downloads")),  # private items are Parlor's own downloads, YouTube's for now
    ("/api/seerr/users", ADMIN), ("/api/seerr/", ("media",)),
    ("/api/arr/music/", ("music",)), ("/api/arr/", ("media",)),
    ("/api/jf/music/", ("music",)), ("/api/jf/", ("library",)),
    # music-fndr: everything that queues downloads or moves files needs Downloads as well
    ("/music/api/downloads", ("music", "downloads")), ("/music/api/import", ("music", "downloads")),
    ("/music/api/organize", ("music", "downloads")), (re.compile(r"/music/api/playlists/[^/]+/sync"), ("music", "downloads")),
    ("/music/api/", ("music",)), ("/rest/", ("music",)),
    ("/api/users", ADMIN), ("/api/settings", ADMIN), ("/api/stack", ADMIN), ("/api/qbit", ADMIN), ("/qbit", ADMIN),
    ("/api/access/", ADMIN), ("/api/vault/key", ADMIN), ("/api/vault/passphrase", ADMIN),
    ("/api/health", EVERYONE), ("/api/auth/", EVERYONE), ("/api/vault/", EVERYONE), ("/api/me/app-passwords", EVERYONE),
    ("/api/me/password", EVERYONE), ("/api/me", EVERYONE),
]
ADMIN_ITSELF = ("/qbit", "/api/vault/key", "/api/vault/passphrase")  # check core.admin inside, not as a Depends


def rule(path: str) -> Optional[tuple]:
    path = re.sub(r"\{[^}]*\}", "x", path)  # a route's own pattern, as the route walk passes it
    for prefix, need in RULES:
        if prefix.fullmatch(path) if isinstance(prefix, re.Pattern) else path == prefix.rstrip("/") or path.startswith(prefix):
            return need
    return None


def checks_admin_itself(path: str) -> bool:
    return path.startswith(ADMIN_ITSELF)


def blocked(user_id: int) -> set:
    row = one("SELECT blocked FROM users WHERE id = ?", user_id)
    return {s for s in ((row or {}).get("blocked") or "").split(",") if s}


def allowed(user: dict, path: str) -> Optional[str]:
    """None when this person may use this path, else the name of the section they lack."""
    need = rule(path)
    if need in (None, EVERYONE, ADMIN) or user.get("admin"):  # admin pages decide for themselves
        return None
    lacking = [s for s in need if s in blocked(user["id"])]
    return SECTIONS[lacking[0]] if lacking else None


def view(user: dict) -> dict:
    """What /api/me says about it, so the page hides what the server refuses."""
    off = set() if user.get("admin") else blocked(user["id"])
    row = one("SELECT libraries FROM users WHERE id = ?", user["id"]) or {}
    return {"blocked": sorted(off), "sections": {s: s not in off for s in SECTIONS},
            "libraries": None if user.get("admin") or row.get("libraries") is None else json.loads(row["libraries"])}


# --- the admin's editor ----------------------------------------------------------------------

router = APIRouter(prefix="/api")


class Access(BaseModel):
    blocked: Optional[list[str]] = None
    libraries: Optional[list[str]] = ["*"]  # None: every library; a list: only these; left out: unchanged


@router.put("/users/{user_id}/access")
def set_access(user_id: int, body: Access, _=Depends(admin)):
    person = one("SELECT id, name, admin FROM users WHERE id = ?", user_id)
    if not person:
        raise HTTPException(404, "No such account")
    if person["admin"]:
        raise HTTPException(409, "Admins always have everything; make them a member first")
    if body.blocked is not None:
        if any(s not in SECTIONS for s in body.blocked):
            raise HTTPException(422, "Unknown section")
        db().execute("UPDATE users SET blocked = ? WHERE id = ?", (",".join(sorted(set(body.blocked))), user_id))
    if body.libraries != ["*"]:
        import jfaccess  # late: it talks to Jellyfin
        jfaccess.limit(person, body.libraries)
    return view(person)


@router.get("/access/libraries")
def libraries(_=Depends(admin)):
    import jfaccess
    return {"libraries": jfaccess.libraries()}
