"""Jellyfin library limits, held by Jellyfin itself: a limited person plays as a Jellyfin user of their own
(their Jellyfin sign-in, else one Parlor makes, hidden and with a random password nobody needs), and that user's
policy lists the libraries they may see. Search, Next up, pictures and streams then all follow it, in Parlor and in
any Jellyfin app. Someone sharing the default Jellyfin user is given their own first, so the others keep theirs."""
import json
import secrets
import threading

from fastapi import HTTPException

import jf
from core import db, one

_lock = threading.Lock()  # one change at a time: a double-click on Save mustn't make two Jellyfin users


def libraries() -> list:
    return [{"id": f["ItemId"], "name": f["Name"], "type": f.get("CollectionType") or ""}
            for f in jf.call("GET", "/Library/VirtualFolders").json() if f.get("ItemId")]


def lift(person):
    """No limits: an admin has everything (on promotion), best effort for the Jellyfin side."""
    db().execute("UPDATE users SET blocked = '' WHERE id = ?", (person["id"],))
    try:
        limit(person, None)
    except HTTPException:
        db().execute("UPDATE users SET libraries = NULL WHERE id = ?", (person["id"],))  # an admin plays as anyone anyway


def forget(person):
    """The person is being deleted: so is the Jellyfin user Parlor made for them alone."""
    row = one("SELECT jf_user, jf_own FROM users WHERE id = ?", person["id"]) or {}
    if row.get("jf_own") and row.get("jf_user"):
        try:
            jf.call("DELETE", f"/Users/{row['jf_user']}")
        except HTTPException:
            pass  # Jellyfin down: a hidden user with a random password, harmless; an admin can remove it there


def _own_user(person) -> str:
    """Their own Jellyfin user's id: their sign-in, the one Parlor made for them, or a new one."""
    row = one("SELECT name, jellyfin_id, jf_user, jf_own FROM users WHERE id = ?", person["id"])
    if row["jellyfin_id"]:
        return row["jellyfin_id"]
    users = jf.call("GET", "/Users").json()
    if row["jf_own"] and any(u["Id"] == row["jf_user"] for u in users):
        return row["jf_user"]  # (one deleted in Jellyfin since is made again below)
    taken = {u["Name"].lower() for u in users}
    name = next(n for n in (f"parlor-{row['name']}-{i}" if i else f"parlor-{row['name']}" for i in range(100)) if n.lower() not in taken)
    made = jf.call("POST", "/Users/New", json={"Name": name, "Password": secrets.token_urlsafe(24)}).json()
    db().execute("UPDATE users SET jf_user = ?, jf_own = 1 WHERE id = ?", (made["Id"], person["id"]))
    jf._users.drop("all")
    return made["Id"]


def limit(person, ids):
    """Only these libraries (a list), or every one (None). Until Jellyfin has taken a limit, the person sees no
    library at all: a failure never leaves them seeing everything."""
    with _lock:
        _limit(person, ids)
    with jf._seen.lock:  # items they could see a moment ago may be hidden now
        jf._seen.data.clear()


def _limit(person, ids):
    before = one("SELECT libraries FROM users WHERE id = ?", person["id"])["libraries"]
    if ids is None and before is None:
        return  # already every library: nothing to change
    if ids is not None:
        db().execute("UPDATE users SET libraries = '[]' WHERE id = ?", (person["id"],))  # closed until it's done
        known = {lib["id"] for lib in libraries()}
        if any(i not in known for i in ids):
            db().execute("UPDATE users SET libraries = ? WHERE id = ?", (before, person["id"]))
            raise HTTPException(422, "Jellyfin has no such library")
    uid = _own_user(person)
    users = jf.call("GET", "/Users").json()
    policy = dict(next((u.get("Policy") or {} for u in users if u["Id"] == uid), {}))
    policy.update(EnableAllFolders=ids is None, EnabledFolders=list(ids or []))
    row = one("SELECT jf_own, jf_user, jellyfin_id FROM users WHERE id = ?", person["id"])
    if row["jf_own"] and uid == row["jf_user"] and not row["jellyfin_id"]:
        policy["IsHidden"] = True  # only the user Parlor made: someone's own Jellyfin account stays as it was
    jf.call("POST", f"/Users/{uid}/Policy", json=policy)
    jf._users.drop("all")
    db().execute("UPDATE users SET libraries = ? WHERE id = ?", (None if ids is None else json.dumps(list(ids)), person["id"]))
